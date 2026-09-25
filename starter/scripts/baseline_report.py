#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""跑公开题库 -> eval/baseline_report.json + 按 category 分解的 markdown 表。

这个脚本只做两件事：把 ``eval/run_eval.py`` 跑一遍，然后把它写出的
``report.json`` 按 ``category`` 重新聚合。**分类不写死在脚本里**——类别、
分值、题号一律从题库文件和评测报告里读，所以知识库或题库换了它照样能跑。

用法（服务要先起起来）：

    python scripts/baseline_report.py --base-url http://localhost:8000
    python scripts/baseline_report.py --markdown   # 只打印表，不跑评测

产物：

    eval/baseline_report.json   机器可读：总分、分类分解、逐题明细、题面
    eval/_baseline_raw/report.json  评测脚本的原始报告（不动它，留作证据）
    stdout                      markdown 表，直接贴进 EVAL_REPORT.md
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # starter/scripts
STARTER = HERE.parent                            # starter/
WORKSPACE = STARTER.parent                       # 作业包根

DEFAULT_QUESTIONS = WORKSPACE / "eval" / "public_questions.jsonl"
DEFAULT_RAW_DIR = WORKSPACE / "eval" / "_baseline_raw"
DEFAULT_OUT = WORKSPACE / "eval" / "baseline_report.json"

#: 题面/检查项里带这些键时，逐题明细会一并带上，方便对着报告定位。
_QUESTION_KEYS = ("id", "category", "points", "note", "turns", "query", "request")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="公开题库基线：跑分 + 分类分解")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    parser.add_argument("--kb", default=str(WORKSPACE / "knowledge_base"))
    parser.add_argument("--raw-out", default=str(DEFAULT_RAW_DIR),
                        help="run_eval.py 的原始报告目录")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--markdown", action="store_true",
                        help="不跑评测，只用已有的 --raw-out/report.json 打印表")
    parser.add_argument("--title", default="基线",
                        help="表标题，例如「P1 之后」")
    return parser.parse_args(argv)


def load_questions(path: Path) -> dict[str, dict]:
    """读题库，返回 {题号: 题目}。读不到就返回空表——聚合不该因为题面缺失而失败。"""
    questions: dict[str, dict] = {}
    if not path.exists():
        print("提醒：题库不存在 %s，逐题明细里不含题面" % path, file=sys.stderr)
        return questions
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict) and item.get("id"):
                questions[str(item["id"])] = item
    return questions


def run_eval(args) -> int:
    """跑评测脚本。返回它的退出码。"""
    raw_dir = Path(args.raw_out)
    raw_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, str(WORKSPACE / "eval" / "run_eval.py"),
        "--base-url", args.base_url,
        "--questions", str(Path(args.questions).resolve()),
        "--kb", str(Path(args.kb).resolve()),
        "--out", str(raw_dir),
        "--timeout", str(args.timeout),
    ]
    print("$ " + " ".join(command))
    return subprocess.call(command, cwd=str(WORKSPACE))


def load_report(raw_dir: Path) -> dict:
    path = raw_dir / "report.json"
    if not path.exists():
        raise SystemExit(
            "找不到 %s——先不带 --markdown 跑一次，或用 --raw-out 指向已有报告。"
            % path
        )
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def aggregate(report: dict, questions: dict[str, dict]) -> dict:
    """按 category 聚合。每一类都把逐题明细带上，便于看是哪些题在拖分。"""
    buckets: dict[str, dict] = {}
    # 先按评测脚本给出的顺序建桶（per_category 已经是排好序的），
    # 再补上题库里有、报告里没跑到的类别。
    for category in list((report.get("per_category") or {}).keys()):
        buckets[category] = _empty_bucket()
    for category in (q.get("category", "") for q in questions.values()):
        buckets.setdefault(category or "unknown", _empty_bucket())

    for item in report.get("questions") or []:
        category = item.get("category") or "unknown"
        bucket = buckets.setdefault(category, _empty_bucket())
        points = float(item.get("points") or 0.0)
        earned = float(item.get("earned") or 0.0)
        question = questions.get(str(item.get("id"))) or {}
        failed_checks = sorted({
            check.get("name")
            for turn in item.get("turns") or []
            for check in turn.get("checks") or []
            if not check.get("passed")
        })
        bucket["got"] += earned
        bucket["total"] += points
        bucket["questions"] += 1
        bucket["passed"] += 1 if item.get("passed") else 0
        bucket["per_question"].append({
            "id": item.get("id"),
            "got": round(earned, 2),
            "max": round(points, 2),
            "passed": bool(item.get("passed")),
            "failed_checks": failed_checks,
            "question": _question_digest(question),
        })

    for bucket in buckets.values():
        bucket["got"] = round(bucket["got"], 2)
        bucket["total"] = round(bucket["total"], 2)
        bucket["ratio"] = round(bucket["got"] / bucket["total"], 4) if bucket["total"] else 0.0
        bucket["per_question"].sort(key=lambda q: (q["passed"], str(q["id"])))

    total = report.get("total") or {}
    return {
        "generated_at": report.get("generated_at"),
        "base_url": report.get("base_url"),
        "questions_file": report.get("questions_file"),
        "only": report.get("only"),
        "llm_mode": (report.get("health") or {}).get("llm_mode"),
        "health": report.get("health"),
        "latency_seconds": report.get("latency_seconds"),
        "total": {
            "got": round(float(total.get("earned") or 0.0), 2),
            "max": round(float(total.get("points") or 0.0), 2),
            "ratio": float(total.get("ratio") or 0.0),
            "questions": total.get("questions"),
            "passed": total.get("passed"),
        },
        "by_category": buckets,
    }


def _empty_bucket() -> dict:
    return {"got": 0.0, "total": 0.0, "ratio": 0.0,
            "questions": 0, "passed": 0, "per_question": []}


def _question_digest(question: dict) -> dict:
    """题面摘要：只留定位用的字段，检查项（expect/checks）不进报告。"""
    digest: dict = {}
    for key in _QUESTION_KEYS:
        if key not in question:
            continue
        if key == "turns":
            digest["turns"] = [
                turn.get("question") for turn in question["turns"]
                if isinstance(turn, dict)
            ]
        elif key == "request":
            digest["request"] = question["request"]
        else:
            digest[key] = question[key]
    return digest


def render_markdown(aggregated: dict, title: str) -> str:
    total = aggregated["total"]
    lines = [
        "### %s：%.2f / %.2f（%.1f%%），%s 题全绿 / 共 %s 题"
        % (title, total["got"], total["max"], total["ratio"] * 100,
           total["passed"], total["questions"]),
        "",
        "| 类别 | 得分 | 满分 | 比例 | 全绿 | 失分题（未通过的检查项） |",
        "|---|---|---|---|---|---|",
    ]
    for category, bucket in aggregated["by_category"].items():
        lost = [
            "%s(%s)" % (q["id"], "/".join(q["failed_checks"][:2]) or "?")
            for q in bucket["per_question"] if not q["passed"]
        ]
        lines.append("| `%s` | %.2f | %.2f | %.1f%% | %d / %d | %s |" % (
            category, bucket["got"], bucket["total"], bucket["ratio"] * 100,
            bucket["passed"], bucket["questions"],
            "、".join(lost) if lost else "—"))
    lines.append("| **合计** | **%.2f** | **%.2f** | **%.1f%%** | **%s / %s** | |" % (
        total["got"], total["max"], total["ratio"] * 100,
        total["passed"], total["questions"]))
    lines.append("")
    health = aggregated.get("health") or {}
    if health:
        lines.append("`/api/health` 快照：`llm_mode=%s`、`kb_docs=%s`、"
                     "`kb_chunks=%s`、`valid_sales_rows=%s`" % (
                         health.get("llm_mode"), health.get("kb_docs"),
                         health.get("kb_chunks"), health.get("valid_sales_rows")))
        lines.append("")
    latency = aggregated.get("latency_seconds") or {}
    if latency.get("median") is not None:
        lines.append("每题耗时：中位数 %.2f 秒，最大 %.2f 秒，合计 %.1f 秒。" % (
            latency["median"], latency["max"], latency["total"]))
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    args = parse_args(argv)
    raw_dir = Path(args.raw_out)

    if not args.markdown:
        code = run_eval(args)
        if code != 0:
            print("提醒：评测脚本退出码 %d，仍然尝试读取报告" % code, file=sys.stderr)

    report = load_report(raw_dir)
    questions = load_questions(Path(args.questions))
    aggregated = aggregate(report, questions)
    aggregated["title"] = args.title
    aggregated["raw_report"] = str((raw_dir / "report.json").resolve())

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        json.dump(aggregated, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print()
    print(render_markdown(aggregated, args.title))
    print("已写入 %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
