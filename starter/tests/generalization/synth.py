"""Generalization Round 1 的合成夹具。

原则（对应本轮任务书 §30/§33）：

* **完全合成的数据与知识库**——门店 S91/S92、商品 P91/P92、日期与金额都是
  自己造的，公开数据集里的任何数字、门店、商品、别名都不进断言；
* 不碰仓库真实的 ``data/`` 与 ``knowledge_base/``，全部写进 ``tmp_path``；
* 期望值来自"机制应当如何"，而不是"公开题库的金标是什么"。
"""

from __future__ import annotations

import socket
import sqlite3
import sys
from pathlib import Path

STARTER = Path(__file__).resolve().parents[2]        # starter/
if str(STARTER) not in sys.path:
    sys.path.insert(0, str(STARTER))

#: 与公开 pos.db 完全相同的表结构（列名/类型一致，这是"同结构换数据"的含义）。
POS_SCHEMA = """
CREATE TABLE stores (store_id TEXT PRIMARY KEY, store_name TEXT, category TEXT, district TEXT);
CREATE TABLE products (product_id TEXT PRIMARY KEY, product_name TEXT,
                       product_category TEXT, unit_price REAL);
CREATE TABLE sales (order_id TEXT, date TEXT, store_id TEXT, product_id TEXT,
                    qty TEXT, amount TEXT, payment TEXT);
"""

DEFAULT_STORES = [
    ("S91", "夜航食堂", "合成品类", "合成区"),
    ("S92", "晨星小馆", "合成品类", "合成区"),
]

DEFAULT_PRODUCTS = [
    ("P91", "Synthetic Tea", "饮品", 12.5),
    ("P92", "Beacon Burger", "主食", 25.0),
]


def make_pos_db(path, sales, stores=None, products=None) -> Path:
    """造一个合成 pos.db。`sales` 是七字段 dict 或元组的列表。"""
    rows = [tuple(s[f] for f in SALE_FIELDS) if isinstance(s, dict) else s
            for s in sales]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(POS_SCHEMA)
        conn.executemany("INSERT INTO stores VALUES (?,?,?,?)",
                         stores if stores is not None else DEFAULT_STORES)
        conn.executemany("INSERT INTO products VALUES (?,?,?,?)",
                         products if products is not None else DEFAULT_PRODUCTS)
        conn.executemany("INSERT INTO sales VALUES (?,?,?,?,?,?,?)", rows)
        conn.commit()
    finally:
        conn.close()
    return path


SALE_FIELDS = ("order_id", "date", "store_id", "product_id", "qty", "amount", "payment")


def sale(order_id="ORD1", date="2026-07-01", store_id="S91", product_id="P91",
         qty="2", amount="30.00", payment="现金") -> dict:
    """一行销售明细的便捷构造器（dict 形态，clean_rows 直接可用）。"""
    return {"order_id": order_id, "date": date, "store_id": store_id,
            "product_id": product_id, "qty": qty, "amount": amount,
            "payment": payment}


# --- 合成知识库 ---------------------------------------------------------------

def write_doc(kb_dir, name: str, text: str, encoding: str = "utf-8") -> Path:
    path = Path(kb_dir) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode(encoding))
    return path


def md_doc(doc_id: str, title: str, body: str, status: str = "现行",
           effective: str = "2026-05-01", extra: str = "") -> str:
    """带 YAML 头的合成 Markdown 文档。"""
    front = ("---\ndoc_id: %s\ntitle: %s\ntype: 通知\nstatus: %s\n"
             "effective_from: %s\n%s---\n\n" % (doc_id, title, status, effective, extra))
    return front + body


def make_kb(kb_dir) -> Path:
    """最小合成知识库：两篇 Markdown。返回目录。"""
    kb_dir = Path(kb_dir)
    write_doc(kb_dir, "KB-901_合成政策.md", md_doc(
        "KB-901", "合成政策",
        "自 2026 年 7 月 1 日起，合成门店的会员积分有效期为 18 个月。\n"))
    write_doc(kb_dir, "KB-902_合成指引.md", md_doc(
        "KB-902", "合成指引",
        "夜航食堂每周二进行设备检修，检修期间暂停外卖接单。\n"))
    return kb_dir


def free_port() -> int:
    """挑一个空闲端口（fresh-process 端到端测试用）。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
