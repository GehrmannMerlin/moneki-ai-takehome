#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""评测即回归（P4 §5）：一键跑「单元测试 → 起服务 → 题库评测 → 分类对比」。

第四关"评测即回归"要求的落地：每次改动后一条命令回答两个问题——
**总分变了没有？哪一类变了？** 对比基准是 ``eval/baseline_report.json``
（接手时原样 starter 的 17.00 基线），类别、分值、题号都从题库与报告里读，
脚本里不写死任何数字。

用法（从 starter/ 目录）：

    .venv/Scripts/python scripts/regression.py                 # 公开题库，与基线对比
    .venv/Scripts/python scripts/regression.py --questions ../eval/extra_questions.jsonl
    .venv/Scripts/python scripts/regression.py --skip-tests    # 跳过 pytest
    .venv/Scripts/python scripts/regression.py --port 8011     # 换端口（默认 8011）

行为：

* 自己在 ``--port`` 上起服务（子进程），跑完负责关掉；不占用评审默认的 8000。
* 退出码：任何一类**低于基线**时返回 1（基线是地板，只能比它高），否则 0。
* 原始报告写到 ``eval/_regression_raw/``，分类对比表打到 stdout（markdown）。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent          # starter/scripts
STARTER = HERE.parent                            # starter/
WORKSPACE = STARTER.parent                       # 作业包根
EVAL_DIR = WORKSPACE / "eval"
BASELINE_JSON = EVAL_DIR / "baseline_report.json"
OUT_DIR = EVAL_DIR / "_regression_raw"


def wait_for_health(base_url: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base_url + "/api/health", timeout=3) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:  # noqa: BLE001 - 服务还没起来，继续等
            time.sleep(0.5)
    raise SystemExit("服务在 %.0f 秒内没起来，回归中止" % timeout)


def run_pytest() -> bool:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=STARTER,
        capture_output=True,
        text=True,
        # **显式指定 UTF-8**：Windows 上 subprocess 默认用系统代码页（GBK）解码，
        # 而 pytest 的输出里有中文（测试名、断言消息），会抛
        # `UnicodeDecodeError: 'gbk' codec can't decode byte ...`。
        # 这个异常发生在读取线程里，脚本**看起来照常跑完**（只判断 returncode），
        # 但输出读不全——典型的静默降级，所以两个子进程调用都要带上。
        encoding="utf-8",
        errors="replace",
    )
    tail = (proc.stdout or "").strip().splitlines()[-1:]
    print("[pytest]", tail[0] if tail else "(无输出)")
    if proc.returncode != 0 and proc.stdout:
        print(proc.stdout[-3000:])
    return proc.returncode == 0


def run_eval(base_url: str, questions: Path) -> dict:
    OUT_DIR.mkdir(exist_ok=True)
    proc = subprocess.run(
        [
            sys.executable,
            str(EVAL_DIR / "run_eval.py"),
            "--base-url", base_url,
            "--questions", str(questions),
            "--out", str(OUT_DIR),
        ],
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
        encoding="utf-8",          # 同上：评测脚本的输出是中文
        errors="replace",
    )
    if proc.returncode != 0:
        print((proc.stdout or "")[-2000:])
        print((proc.stderr or "")[-2000:])
        raise SystemExit("评测脚本运行失败")
    return json.loads((OUT_DIR / "report.json").read_text(encoding="utf-8"))


def by_category(report: dict) -> dict:
    """按 category 聚合 earned/points（从报告里读，不写死类别）。"""
    table: dict[str, dict[str, float]] = {}
    for question in report["questions"]:
        bucket = table.setdefault(question["category"], {"earned": 0.0, "points": 0.0, "failed": []})
        bucket["earned"] += question["earned"]
        bucket["points"] += question["points"]
        if question["earned"] < question["points"]:
            bucket["failed"].append(question["id"])
    return table


def main() -> int:
    parser = argparse.ArgumentParser(description="评测即回归")
    parser.add_argument("--questions", default=str(EVAL_DIR / "public_questions.jsonl"),
                        help="题库路径（相对路径按当前目录解析，建议用绝对路径）")
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()
    questions = Path(args.questions).resolve()
    if not questions.exists():
        raise SystemExit("题库不存在：%s" % questions)

    tests_ok = True
    if not args.skip_tests:
        tests_ok = run_pytest()

    base_url = "http://127.0.0.1:%d" % args.port
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "kbqa.server:app", "--host", "127.0.0.1", "--port", str(args.port)],
        cwd=STARTER,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        health = wait_for_health(base_url)
        print("[health] llm_mode=%s kb_docs=%s valid_sales_rows=%s" % (
            health.get("llm_mode"), health.get("kb_docs"), health.get("valid_sales_rows")))
        report = run_eval(base_url, questions)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    current = by_category(report)
    total_earned = sum(item["earned"] for item in current.values())
    total_points = sum(item["points"] for item in current.values())

    baseline = {}
    if BASELINE_JSON.exists():
        raw = json.loads(BASELINE_JSON.read_text(encoding="utf-8"))
        # baseline_report.json 的结构是 by_category{got,total,...}，不是评测原始报告。
        baseline = {
            category: {"earned": item.get("got", 0.0), "points": item.get("total", 0.0), "failed": []}
            for category, item in raw.get("by_category", {}).items()
        }

    lines = ["| 类别 | 基线 | 本次 | 满分 | 变化 | 失分题 |", "|---|---|---|---|---|---|"]
    regressed = []
    for category in sorted(set(baseline) | set(current)):
        now = current.get(category, {"earned": 0.0, "points": 0.0, "failed": []})
        base = baseline.get(category, {"earned": 0.0})
        delta = now["earned"] - base["earned"]
        mark = "—" if abs(delta) < 1e-9 else ("↑ %.2f" % delta if delta > 0 else "↓ %.2f" % delta)
        # 回退判定以"本题库该类的满分"封顶基线：换一套题（如自补题）时类别总分不同，
        # 拿基线原始分硬比会把"题库里根本没这类题"误报成回退。
        floor = min(base["earned"], now["points"])
        if now["earned"] < floor - 1e-9:
            regressed.append(category)
        lines.append("| %s | %.2f | **%.2f** | %.2f | %s | %s |" % (
            category, base["earned"], now["earned"], now["points"], mark,
            "、".join(now["failed"]) or "无"))
    lines.append("| **合计** | **%.2f** | **%.2f** | **%.2f** | | |" % (
        sum(item["earned"] for item in baseline.values()) if baseline else 0.0,
        total_earned, total_points))

    print("\n".join(lines))
    print("\n题库：%s ｜ 原始报告：%s" % (args.questions, OUT_DIR / "report.json"))

    if regressed:
        print("⚠️ 以下类别低于基线：%s" % "、".join(regressed))
        return 1
    if not tests_ok:
        print("⚠️ 单元测试有失败（见上）")
        return 1
    print("✅ 无回退：所有类别不低于基线，单元测试通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
