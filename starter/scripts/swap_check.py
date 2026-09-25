#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""换库自验：造一份"结构相同、数字不同、文档有增有改"的变体，验证零写死。

**为什么必须做这一步**：作业 README 的评分流程第 3 步是——

> 把 `data/` 和 `knowledge_base/` 换成我们手里的另一份（结构相同，数字不同，
> 文档有增有改），执行你的重建命令，再用隐藏题库跑一遍。

也就是说：**这一整套流程评委一定会跑，而公开题库跑满分完全不能保证它不炸。**
任何写死的数字、答案、文档编号、门店/商品清单，都会在这一步暴露。

做法：
1. 把 `data/pos.db` 复制到临时目录（**不碰仓库原件**），按固定规则改数字：
   * 把若干行的 `amount` 乘 2（可预测地改变营业额）；
   * 删掉若干行（可预测地改变行数）；
   * 改一个门店名（验证门店名是从库里读的，不是写死的）。
2. 把 `knowledge_base/` 复制到临时目录，改两件事：
   * 给一份**现行**文档改一个关键数字（验证引用跟着新文档走）；
   * **新增**一份带新编号的文档（验证索引能感知知识库变化、新文档可被检索到）。
3. 用变体目录跑 `build_index` + `build_clean_db`，断言：
   * 缓存键变了（否则会读旧索引）；
   * 清洗行数 / 指标 / `kb_docs` 都跟着变；
   * 新文档能被检索到并被引用；
   * 改过的数字出现在回答里，**旧数字不再出现**。

全部通过则说明"没有把这一份数据的事实写进代码"。

用法（从 starter/ 目录）：

    .venv/Scripts/python scripts/swap_check.py
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent          # starter/scripts
STARTER = HERE.parent
WORKSPACE = STARTER.parent
sys.path.insert(0, str(STARTER))

KB_SRC = WORKSPACE / "knowledge_base"
DB_SRC = WORKSPACE / "data" / "pos.db"


def build_variant_data(target_db: Path, *, keep_ratio: float = 0.9) -> dict:
    """复制 pos.db 并改数字：删掉尾部一部分行 + 把一个门店改名。

    改法是**可预测的**，这样期望值能自己算出来，不用碰运气。
    """
    target_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(DB_SRC, target_db)
    conn = sqlite3.connect(str(target_db))
    try:
        before = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        # 按 date 排序删掉最后 10% 的行：既改行数，也改数据区间
        keep = int(before * keep_ratio)
        conn.execute(
            "DELETE FROM sales WHERE rowid IN ("
            "  SELECT rowid FROM sales ORDER BY date DESC, rowid DESC LIMIT ?)",
            (before - keep,),
        )
        # 改一个门店名：门店名必须从库里读，不能写死
        conn.execute("UPDATE stores SET store_name = ? WHERE store_id = 'S01'",
                     ("Super Souper 改名验证店",))
        # 改一个商品的品类：品类聚合必须跟着变
        conn.execute("UPDATE products SET product_category = ? WHERE product_id = 'P01'",
                     ("变体品类",))
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        period = conn.execute("SELECT MIN(date), MAX(date) FROM sales").fetchone()
        store = conn.execute(
            "SELECT store_name FROM stores WHERE store_id='S01'").fetchone()[0]
    finally:
        conn.close()
    return {"rows_before": before, "rows_after": after,
            "period": (period[0], period[1]), "store_name": store}


def build_variant_kb(target_kb: Path) -> dict:
    """复制知识库，改一份现行文档的关键数字，并新增一份文档。"""
    if target_kb.exists():
        shutil.rmtree(target_kb)
    shutil.copytree(KB_SRC, target_kb)

    # ① 改现有文档：KB-013 的 24 小时 → 48 小时
    victim = next(target_kb.rglob("KB-013*"))
    text = victim.read_text(encoding="utf-8")
    assert "24 小时" in text, "前提不成立：KB-013 里没有 '24 小时'"
    victim.write_text(text.replace("24 小时", "48 小时"), encoding="utf-8")

    # ② 新增一份文档：带新编号、新事实
    new_doc = target_kb / "notices" / "KB-901_变体新增通知.md"
    new_doc.write_text(
        "---\ndoc_id: KB-901\ntitle: 变体新增通知\n"
        "type: 通知\nstatus: 现行\neffective_from: 2026-08-20\n---\n\n"
        "# 变体新增通知\n\n"
        "自 2026 年 8 月 20 日起，所有门店的打包盒统一更换为可降解材质，"
        "单个成本 1.25 元，由总部统一采购。\n",
        encoding="utf-8",
    )
    return {"edited": "KB-013（24 小时 → 48 小时）", "added": "KB-901"}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="swapcheck-"))
    print("变体目录：%s\n" % tmp)

    data_dir = tmp / "data"
    kb_dir = tmp / "kb"
    print("[1] 造变体数据")
    data_info = build_variant_data(data_dir / "pos.db")
    for key, value in data_info.items():
        print("    %-14s %s" % (key, value))
    print("\n[2] 造变体知识库")
    kb_info = build_variant_kb(kb_dir)
    for key, value in kb_info.items():
        print("    %-14s %s" % (key, value))

    from kbqa.core.cleaning import build_clean_db
    from kbqa.core.index import build_index, content_key
    from kbqa.core.metrics import MetricsEngine
    from kbqa.core.retriever import Retriever
    from datetime import date

    print("\n[3] 用变体目录重建（评委第 3 步做的事）")
    real_key = content_key(KB_SRC)
    variant_key = content_key(kb_dir)
    print("    缓存键  原库 %s  →  变体 %s" % (real_key[:12], variant_key[:12]))
    assert real_key != variant_key, "换了知识库缓存键却没变——会读旧索引"

    clean_db = tmp / "var" / "clean.db"
    report = build_clean_db(data_dir / "pos.db", clean_db)
    index = build_index(kb_dir)
    engine = MetricsEngine(clean_db)
    print("    valid_sales_rows %d（变体原始 %d 行）"
          % (report.kept_rows, report.raw_rows))
    print("    kb_docs %d  kb_chunks %d" % (len(index.docs_meta), len(index.chunks)))
    print("    data_period %s" % engine.data_period())

    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print("    %s %s%s" % ("OK  " if ok else "FAIL", label,
                               ("  —— " + detail) if detail and not ok else ""))
        if not ok:
            failures.append(label)

    print("\n[4] 断言：一切跟着变体走")
    check("入库文档数 = 36（35 + 新增 1）",
          len(index.docs_meta) == 36, "实际 %d" % len(index.docs_meta))
    check("新增的 KB-901 进了索引", "KB-901" in index.docs_meta)
    check("保留行数与清洗结果一致",
          report.kept_rows + report.sum_removed() == data_info["rows_after"]
          if hasattr(report, "sum_removed") else
          report.kept_rows + sum(report.removed.values()) == data_info["rows_after"])

    # 门店名必须从库里读
    stores = {s["store_id"]: s["store_name"] for s in engine.stores()}
    check("门店名跟着变体走（S01 改名）",
          stores.get("S01") == data_info["store_name"], "实际 %r" % stores.get("S01"))

    # 新增文档必须能被检索到
    retriever = Retriever(index, date(2026, 9, 1))
    hits = [h.doc_id for h in retriever.search("打包盒 可降解 成本", top_k=5).hits]
    check("新增文档能被检索到", "KB-901" in hits, "top5=%s" % hits)

    # 改过的文档：新数字能查到，旧数字不再出现在该文档里
    kb901 = index.texts.get("KB-901", "")
    check("新文档正文可读", "1.25" in kb901)
    kb013 = index.texts.get("KB-013", "")
    check("改过的文档用新数字（48 小时）", "48 小时" in kb013)
    check("旧数字不再出现在该文档", "24 小时" not in kb013)

    # 指标跟着变体走：区间末日被删掉了一批行
    variant_period = engine.data_period()
    check("数据区间跟着变体走（末日提前）",
          variant_period["end"] <= data_info["period"][1])

    # 缓存往返仍然等价（P2 的 D13c 回归在变体上也要成立）
    from kbqa.core.index import load_index
    cache = tmp / "cache" / "index.json"
    written = load_index(kb_dir, cache, rebuild=True)
    loaded = load_index(kb_dir, cache)
    check("变体下缓存往返 postings 一致",
          set(written.postings) == set(loaded.postings))

    # ---- 端到端：真的在变体上起一个服务，问它问题 ----
    # 上面那些断言证明的只是"数据层跟着变体走"。真正要证的是
    # **问答链路全都不写死**——回答里必须出现变体的新数字，而不是公开题库的旧数字。
    print("\n[5] 端到端：在变体数据上起服务并提问")
    e2e_ok, e2e_detail = run_end_to_end(tmp, kb_dir, data_dir, engine)
    check("变体上问答链路不写死", e2e_ok, e2e_detail)

    print()
    if failures:
        print("换库自验未通过：%s" % "、".join(failures))
        return 1
    print("换库自验通过：没有把这一份数据的事实写进代码。")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


def run_end_to_end(tmp: Path, kb_dir: Path, data_dir: Path, engine) -> tuple[bool, str]:
    """用变体目录起一个真实服务，验证回答跟着变体走。

    返回 `(是否通过, 失败说明)`。
    """
    import os
    import subprocess
    import time
    import urllib.error
    import urllib.request

    port = 8017
    env = dict(os.environ)
    env.update({
        "KB_DIR": str(kb_dir),
        "DATA_DIR": str(data_dir),
        "VAR_DIR": str(tmp / "var_e2e"),
        "PYTHONIOENCODING": "utf-8",
    })
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        env.pop(key, None)                     # 强制 mock 降级模式

    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "kbqa.server:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(STARTER), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = "http://127.0.0.1:%d" % port

    def get(path: str):
        with urllib.request.urlopen(base + path, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def post(path: str, payload: dict):
        request = urllib.request.Request(
            base + path, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"})
        with urllib.request.urlopen(request, timeout=90) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                health = get("/api/health")
                break
            except Exception:                  # noqa: BLE001 - 还没起来
                time.sleep(0.5)
        else:
            return False, "变体服务 90 秒内没起来"

        problems: list[str] = []
        if health.get("kb_docs") != 36:
            problems.append("kb_docs=%s（应 36）" % health.get("kb_docs"))
        if health.get("valid_sales_rows") != engine.valid_sales_rows():
            problems.append("valid_sales_rows=%s（应 %s）"
                            % (health.get("valid_sales_rows"), engine.valid_sales_rows()))

        # ① 改过的文档：必须答 48 小时，不能答 24 小时
        answer = post("/api/chat", {"session_id": "swap-1",
                                    "question": "外卖订单多久内可以申请退款？"})
        text = answer.get("answer", "")
        if "48" not in text:
            problems.append("改过的 KB-013 没生效（回答里没有 48）：%s" % text[:100])
        if "24 小时" in text:
            problems.append("还在用旧数字 24 小时：%s" % text[:100])

        # ② 新增的文档：必须能被问到
        answer2 = post("/api/chat", {"session_id": "swap-2",
                                     "question": "打包盒换成什么材质了？成本多少？"})
        text2 = answer2.get("answer", "")
        cited = {c.get("doc_id") for c in answer2.get("citations", [])}
        if "KB-901" not in cited:
            problems.append("新增文档没被引用（cite=%s）：%s" % (cited, text2[:100]))
        if "1.25" not in text2:
            problems.append("新文档的事实没答出来：%s" % text2[:100])

        # ③ 改名后的门店名必须从库里读
        answer3 = post("/api/chat", {"session_id": "swap-3",
                                     "question": "S01 的净营业额是多少？"})
        text3 = answer3.get("answer", "")
        if "改名验证店" not in text3:
            problems.append("门店名没跟着变体走：%s" % text3[:100])

        return (not problems), "；".join(problems)
    except Exception as exc:                   # noqa: BLE001
        return False, "端到端检查抛异常：%s: %s" % (type(exc).__name__, exc)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:      # pragma: no cover
            server.kill()


if __name__ == "__main__":
    sys.exit(main())
