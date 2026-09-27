"""Dynamic R6 hidden-evaluation stress harness.

The module deliberately keeps its first layer independent from the production
answering stack.  Variant data is generated from a seed and the oracle reads
the generated SQLite source directly.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


POS_SCHEMA = """
CREATE TABLE stores (
    store_id TEXT PRIMARY KEY,
    store_name TEXT,
    category TEXT,
    district TEXT
);
CREATE TABLE products (
    product_id TEXT PRIMARY KEY,
    product_name TEXT,
    product_category TEXT,
    unit_price REAL
);
CREATE TABLE sales (
    order_id TEXT,
    date TEXT,
    store_id TEXT,
    product_id TEXT,
    qty TEXT,
    amount TEXT,
    payment TEXT
);
"""


@dataclass(frozen=True)
class DataVariant:
    """A generated source database and the scope used by its questions."""

    root: Path
    data_dir: Path
    db_path: Path
    seed: int
    family: str
    store_id: str
    product_id: str
    start: str
    end: str


def _generated_ids(seed: int) -> tuple[str, str]:
    # Keep the first generated family in the two-digit range understood by the
    # current entity parser while still deriving the IDs from the seed.
    number = 91 + (abs(seed) % 9)
    return "S%02d" % number, "P%02d" % number


def make_data_variant(root: Path, *, seed: int, family: str = "values") -> DataVariant:
    """Create a deterministic, non-public POS database under ``root``."""
    supported = {"values", "rows", "entities", "dirty"}
    if family not in supported:
        raise ValueError("unsupported data family: %s" % family)

    root = Path(root)
    data_dir = root / "data"
    db_path = data_dir / "pos.db"
    data_dir.mkdir(parents=True, exist_ok=True)
    store_id, product_id = _generated_ids(seed)
    base = 23 + abs(seed) % 17
    rows = [
        ("R6-%d-1" % seed, "2026-07-01", store_id, product_id, "1", "%.2f" % (base * 1.5), "现金"),
        ("R6-%d-2" % seed, "2026-07-02", store_id, product_id, "2", "%.2f" % (base * 2.0), "移动支付"),
        ("R6-%d-3" % seed, "2026-07-03", store_id, product_id, "1", "%.2f" % (base * 3.0), "现金"),
        ("R6-%d-4" % seed, "2026-07-04", store_id, product_id, "3", "%.2f" % (base * 1.25), "银行卡"),
    ]
    end = "2026-07-04"
    stores = [(store_id, "R6 动态门店 %d" % seed, "R6 动态分类", "R6 动态区域")]
    products = [(product_id, "R6 Dynamic Product %d" % seed, "R6 动态商品类", float(base))]
    if family == "rows":
        rows.extend([
            ("R6-%d-5" % seed, "2026-07-05", store_id, product_id, "4", "%.2f" % (base * 2.75), "现金"),
            ("R6-%d-6" % seed, "2026-07-06", store_id, product_id, "1", "%.2f" % (base * 4.25), "移动支付"),
        ])
        end = "2026-07-06"
    elif family == "entities":
        second_store = "S%02d" % (91 + ((abs(seed) + 1) % 9))
        second_product = "P%02d" % (91 + ((abs(seed) + 1) % 9))
        stores.append((second_store, "R6 第二动态门店 %d" % seed, "R6 新分类", "R6 新区域"))
        products.append((second_product, "R6 Second Dynamic Product %d" % seed, "R6 新商品类", float(base + 7)))
        rows.append(("R6-%d-5" % seed, "2026-07-05", second_store, second_product, "2", "%.2f" % (base * 5.5), "现金"))
        end = "2026-07-05"
    elif family == "dirty":
        rows.extend([
            rows[0],
            ("R6-%d-bad-date" % seed, "2026-02-31", store_id, product_id, "1", "9.00", "现金"),
            ("R6-%d-bad-qty" % seed, "2026-07-05", store_id, product_id, "0", "9.00", "现金"),
            ("R6-%d-bad-store" % seed, "2026-07-05", "S00", product_id, "1", "9.00", "现金"),
            ("R6-%d-bad-product" % seed, "2026-07-05", store_id, "P00", "1", "9.00", "现金"),
            ("R6-%d-zero" % seed, "2026-07-05", store_id, product_id, "1", "0.00", "现金"),
        ])
        end = "2026-07-05"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(POS_SCHEMA)
        conn.executemany("INSERT INTO stores VALUES (?, ?, ?, ?)", stores)
        conn.executemany("INSERT INTO products VALUES (?, ?, ?, ?)", products)
        conn.executemany("INSERT INTO sales VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        conn.commit()
    return DataVariant(
        root=root,
        data_dir=data_dir,
        db_path=db_path,
        seed=seed,
        family=family,
        store_id=store_id,
        product_id=product_id,
        start="2026-07-01",
        end=end,
    )


def data_oracle(
    db_path: Path,
    *,
    start: str,
    end: str,
    store_id: str | None = None,
    product_id: str | None = None,
) -> dict[str, Any]:
    """Calculate a valid generated-data summary directly from source SQLite."""
    clauses = ["date >= ?", "date <= ?"]
    params: list[Any] = [start, end]
    if store_id:
        clauses.append("store_id = ?")
        params.append(store_id)
    if product_id:
        clauses.append("product_id = ?")
        params.append(product_id)
    where = " AND ".join(clauses)
    with sqlite3.connect(Path(db_path)) as conn:
        rows = conn.execute(
            "SELECT order_id, qty, amount FROM sales WHERE " + where,
            params,
        ).fetchall()
    amounts = [float(row[2]) for row in rows]
    qty = sum(int(float(row[1])) for row in rows)
    orders = len({row[0] for row in rows})
    revenue = round(sum(amounts), 2)
    return {
        "net_revenue": revenue,
        "qty": qty,
        "orders": orders,
        "aov": round(revenue / orders, 2) if orders else None,
    }
