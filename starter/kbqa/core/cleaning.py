"""清洗层：把原始 `sales` 导进 `clean.db`。

设计（架构决策 5）：**规范化全量表 + 行级双口径剔除标记**，剔除推迟到计算层。
一行只记"首因"——按 KB-001 v3 §3 的规则顺序，第一条命中的规则就是它的原因，
所以六项计数相加等于总剔除数。

口径依据：KB-001 v3 §2（规范化）、§3（剔除）、§5.3（维表滞后）。

独立复算的结果（本机 `data/pos.db`）：

| 规则 | 剔除数 |
|---|---|
| 1 日期无法解析／日历非法 | 8（含 3 行 `'2026-13-45'`） |
| 2 `amount` 为空 | 150 |
| 3 `qty <= 0` | 30 |
| 4 脏门店外键 | 10 |
| 5 脏商品外键 | 40 |
| 6 七字段完全重复 | 100 |
| **合计剔除 / 保留** | **338 / 18290**（18196 销售行 + 94 退款行） |
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Optional

from .normalize import norm_id, parse_amount_cents, parse_date, parse_qty

#: 六条剔除规则，顺序即优先级（首因归因）。
REMOVAL_REASONS = (
    "1_unparseable_date",
    "2_empty_amount",
    "3_qty_le_zero",
    "4_store_not_in_stores",
    "5_product_not_in_products",
    "6_duplicate_row",
)

#: v2 口径独有的剔除原因（退款行在 v2 里不计入净营业额）。
V2_REFUND_EXCLUDED = "v2_refund_excluded"

#: 参与"七字段完全重复"判定的字段（都已规范化）。
#: `amount_cents` 用"分"比较，免得 `'38.00'` 与 `'38.0'` 被当成两行。
_DUP_FIELDS = ("order_id", "date", "store_id", "product_id", "qty", "amount_cents", "payment")


@dataclass
class CleaningReport:
    """清洗台账。形状兼容 starter 的 `CleaningReport.as_dict()`，并扩展 v2 口径。"""

    raw_rows: int = 0
    kept_rows: int = 0
    kept_sales_rows: int = 0
    kept_refund_rows: int = 0
    removed: dict = field(default_factory=lambda: {key: 0 for key in REMOVAL_REASONS})
    note_unparseable_amount: int = 0
    #: 七字段完全重复的组数（不等于被剔除的行数：一组可能有 2 个以上副本）。
    duplicate_groups: int = 0
    #: v2 口径下能回填、因而保留的行数（这些行在 v3 里被剔除）。
    v2_backfilled_rows: int = 0
    #: v2 口径下被剔除的退款行数。
    v2_refund_rows: int = 0

    def removed_total(self) -> int:
        return sum(self.removed.values())

    def as_dict(self) -> dict:
        return {
            "raw_rows": self.raw_rows,
            "removed": dict(self.removed, note_unparseable_amount=self.note_unparseable_amount),
            "kept_rows": self.kept_rows,
            "kept_sales_rows": self.kept_sales_rows,
            "kept_refund_rows": self.kept_refund_rows,
            "duplicate_groups": self.duplicate_groups,
            "v2_backfilled_rows": self.v2_backfilled_rows,
            "v2_refund_rows": self.v2_refund_rows,
            "rejected_rows": self.removed_total(),
        }


@dataclass
class Line:
    """清洗后的中间行，字段与 `clean_lines` 的列一一对应。"""

    order_id: str = ""
    date: Optional[str] = None
    store_id: str = ""
    product_id: str = ""
    qty: Optional[int] = None
    amount_cents: Optional[int] = None
    payment: str = ""
    is_refund: int = 0
    dup_group: Optional[int] = None
    reject_v3: Optional[str] = None
    reject_v2: Optional[str] = None
    raw_date: str = ""
    raw_qty: str = ""
    raw_amount: str = ""
    #: v2 口径下按 `qty × unit_price` 回填的金额（分）。只在 v2 计算层用，不落库。
    backfilled_cents: Optional[int] = None

    def as_tuple(self) -> tuple:
        return tuple(getattr(self, name) for name in COLUMNS)

    def snapshot(self) -> str:
        """审计用的行快照：原始值 + 规范化后的编号，够定位是哪一行。"""
        return json.dumps(
            {
                "order_id": self.order_id,
                "date": self.raw_date,
                "store_id": self.store_id,
                "product_id": self.product_id,
                "qty": self.raw_qty,
                "amount": self.raw_amount,
                "payment": self.payment,
            },
            ensure_ascii=False,
        )


#: `clean_lines` 的列顺序，也是 `Line.as_tuple()` 的顺序。
COLUMNS = (
    "order_id", "date", "store_id", "product_id", "qty", "amount_cents",
    "payment", "is_refund", "dup_group", "reject_v3", "reject_v2",
    "raw_date", "raw_qty", "raw_amount",
)


class CleanResult:
    """`clean_rows()` 的返回值：保留的行、剔除的行、台账、逐条审计。"""

    def __init__(self, kept: list[Line], rejected: list[Line], report: CleaningReport,
                 audit: list[tuple[str, str]]) -> None:
        self.kept = kept
        self.rejected = rejected
        self.report = report
        self.audit = audit

    def __iter__(self):
        """让 `kept, report = clean_rows(...)` 这种老写法继续可用。"""
        return iter((self.kept, self.report))


def clean_rows(
    rows: Iterable,
    stores: set[str],
    products: set[str],
    unit_prices: Optional[dict[str, float]] = None,
) -> CleanResult:
    """按 KB-001 v3 §3 的六条规则清洗。

    每行按规则顺序判定，命中第一条即停（首因归因）。规则 6 的判重作用在
    **已经通过前五条的行**上，所以"既重复又脏外键"的行算脏外键。

    保留行与剔除行都返回：剔除行要落进 `clean_lines` 供数据质量面板与
    调试用（架构决策 5 的"行级双口径剔除标记"）。
    """
    report = CleaningReport()
    kept: list[Line] = []
    rejected: list[Line] = []
    audit: list[tuple[str, str]] = []
    seen: dict[tuple, int] = {}
    group_seq = 0

    for row in rows:
        report.raw_rows += 1
        line = _to_line(row)

        reject = _first_reject(line, stores, products)
        if reject is None:
            key = tuple(getattr(line, name) for name in _DUP_FIELDS)
            if key in seen:
                reject = "6_duplicate_row"
                line.dup_group = seen[key]
            else:
                group_seq += 1
                line.dup_group = group_seq
                seen[key] = group_seq

        line.reject_v3 = reject
        if reject is not None:
            report.removed[reject] += 1
            if reject == "2_empty_amount" and parse_amount_cents(line.raw_amount)[1] == "bad":
                # 金额解析不了的行（不是空、是坏值）：单独记一笔，starter 把它们当 0 保留
                report.note_unparseable_amount += 1
            if reject == "6_duplicate_row":
                report.duplicate_groups += 1
            audit.append((reject, line.snapshot()))
            line.reject_v2 = reject if reject != "2_empty_amount" else None
            rejected.append(line)
        else:
            kept.append(line)

        # v2 口径标记：与 v3 只差两处（退款行剔除、空 amount 回填后保留）
        if reject == "2_empty_amount":
            backfill = backfill_cents(line, unit_prices)
            if backfill is not None:
                line.reject_v2 = None
                line.backfilled_cents = backfill
                report.v2_backfilled_rows += 1
            else:
                line.reject_v2 = "2_empty_amount"
        if line.reject_v2 is None and line.is_refund:
            line.reject_v2 = V2_REFUND_EXCLUDED
            report.v2_refund_rows += 1

    report.kept_rows = len(kept)
    report.kept_refund_rows = sum(1 for line in kept if line.is_refund)
    report.kept_sales_rows = report.kept_rows - report.kept_refund_rows
    return CleanResult(kept, rejected, report, audit)


def _to_line(row) -> Line:
    get = row.get if isinstance(row, dict) else row.__getitem__
    raw_date = _text(get("date"))
    raw_qty = _text(get("qty"))
    raw_amount = _text(get("amount"))
    day = parse_date(raw_date)
    cents, _status = parse_amount_cents(raw_amount)
    return Line(
        order_id=norm_id(get("order_id")),
        date=day.isoformat() if day else None,
        store_id=norm_id(get("store_id")),
        product_id=norm_id(get("product_id")),
        qty=parse_qty(raw_qty),
        amount_cents=cents,
        payment=norm_id(get("payment")),
        is_refund=1 if (cents is not None and cents < 0) else 0,
        raw_date=raw_date,
        raw_qty=raw_qty,
        raw_amount=raw_amount,
    )


def _text(value) -> str:
    return "" if value is None else str(value)


def _first_reject(line: Line, stores: set[str], products: set[str]) -> Optional[str]:
    """按序返回第一条命中的剔除规则；规则 1–5 都不命中返回 None（规则 6 由调用方判）。"""
    if line.date is None:
        return "1_unparseable_date"
    if line.amount_cents is None:
        return "2_empty_amount"
    if line.qty is None or line.qty <= 0:
        return "3_qty_le_zero"
    if line.store_id not in stores:
        return "4_store_not_in_stores"
    if line.product_id not in products:
        return "5_product_not_in_products"
    return None


def backfill_cents(line: Line, unit_prices: Optional[dict[str, float]]) -> Optional[int]:
    """空金额按 `qty × unit_price` 回填，返回"分"；回填不了返回 None。

    这是 **v2 口径**的规则（KB-002），v3 明确不回填。建档价来自 `products.unit_price`。
    """
    if line.qty is None or line.qty <= 0 or not unit_prices:
        return None
    price = unit_prices.get(line.product_id)
    if price is None:
        return None
    return int((Decimal(str(price)) * 100).to_integral_value()) * line.qty


# ---------------------------------------------------------------------------
# 建库
# ---------------------------------------------------------------------------

#: `clean_lines` 是清洗后的全量行（带双口径标记）；
#: `sales_clean` 是它的**兼容视图**，只暴露保留的行。
#: 保留这个视图是为了让 starter 里形状本来就对的 SQL（`payment_mix` /
#: `top_products` / `by_store` / `unit_price_check` / `compare_periods`）
#: 一行不改继续用，改动集中在真正错的那三处口径上。
_SCHEMA = """
CREATE TABLE stores (store_id TEXT PRIMARY KEY, store_name TEXT, category TEXT, district TEXT);
CREATE TABLE products (product_id TEXT PRIMARY KEY, product_name TEXT,
                       product_category TEXT, unit_price REAL);

CREATE TABLE clean_lines (
    rowid_pk     INTEGER PRIMARY KEY,
    order_id     TEXT NOT NULL,
    date         TEXT,
    store_id     TEXT NOT NULL,
    product_id   TEXT NOT NULL,
    qty          INTEGER,
    amount_cents INTEGER,
    payment      TEXT,
    is_refund    INTEGER NOT NULL DEFAULT 0,
    dup_group    INTEGER,
    reject_v3    TEXT,
    reject_v2    TEXT,
    raw_date     TEXT,
    raw_qty      TEXT,
    raw_amount   TEXT
);
CREATE INDEX idx_lines_date    ON clean_lines(date);
CREATE INDEX idx_lines_store   ON clean_lines(store_id);
CREATE INDEX idx_lines_product ON clean_lines(product_id);
CREATE INDEX idx_lines_reject  ON clean_lines(reject_v3);

CREATE VIEW sales_clean AS
    SELECT order_id, date, store_id, product_id, qty, amount_cents, payment, is_refund
    FROM clean_lines WHERE reject_v3 IS NULL;

CREATE TABLE rejects_audit (rule TEXT, line_snapshot TEXT);
CREATE INDEX idx_audit_rule ON rejects_audit(rule);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def open_source(path: Path) -> sqlite3.Connection:
    """打开源库，只读。"""
    conn = sqlite3.connect("file:%s?mode=ro" % Path(path).as_posix(), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def build_clean_db(source: Path, target: Path) -> CleaningReport:
    """从只读源库重建 `clean.db`，返回清洗台账。

    内建**守恒自验**：`保留 + 剔除 == 原始行数` 不成立就当场抛错。
    口径写错时 rebuild 直接炸，而不是把错数字留到评测里。
    """
    source = Path(source)
    if not source.exists():
        raise FileNotFoundError("找不到源数据库：%s" % source)

    src = open_source(source)
    try:
        stores = [
            (norm_id(r["store_id"]), r["store_name"], r["category"], r["district"])
            for r in src.execute("SELECT store_id, store_name, category, district FROM stores")
        ]
        products = [
            (norm_id(r["product_id"]), r["product_name"], r["product_category"], r["unit_price"])
            for r in src.execute(
                "SELECT product_id, product_name, product_category, unit_price FROM products")
        ]
        rows = list(src.execute(
            "SELECT order_id, date, store_id, product_id, qty, amount, payment FROM sales"))
    finally:
        src.close()

    store_ids = {row[0] for row in stores}
    product_ids = {row[0] for row in products}
    unit_prices = {row[0]: row[3] for row in products}

    result = clean_rows(rows, store_ids, product_ids, unit_prices)
    report = result.report

    if report.kept_rows + report.removed_total() != report.raw_rows:
        raise AssertionError(
            "清洗守恒被打破：保留 %d + 剔除 %d = %d，原始 %d"
            % (report.kept_rows, report.removed_total(),
               report.kept_rows + report.removed_total(), report.raw_rows))

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()

    out = sqlite3.connect(str(target))
    try:
        out.executescript(_SCHEMA)
        out.executemany("INSERT INTO stores VALUES (?,?,?,?)", stores)
        out.executemany("INSERT INTO products VALUES (?,?,?,?)", products)
        insert = ("INSERT INTO clean_lines (%s) VALUES (%s)"
                  % (", ".join(COLUMNS), ", ".join("?" * len(COLUMNS))))
        # 保留行与被剔除的行都落库：后者是"行级双口径剔除标记"与数据质量面板的依据
        out.executemany(insert, [line.as_tuple() for line in result.kept])
        out.executemany(insert, [line.as_tuple() for line in result.rejected])
        out.executemany("INSERT INTO rejects_audit VALUES (?,?)", result.audit)
        out.execute("INSERT INTO meta VALUES ('cleaning_report', ?)",
                    (json.dumps(report.as_dict(), ensure_ascii=False),))
        out.execute("INSERT INTO meta VALUES ('source_db', ?)", (source.name,))
        out.commit()
    finally:
        out.close()
    return report
