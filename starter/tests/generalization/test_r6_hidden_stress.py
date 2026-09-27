"""R6 hidden-style stress tests.

These tests intentionally use generated entities and independently calculated
expected values.  They are not another copy of the public evaluator fixtures.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

STARTER = Path(__file__).resolve().parents[2]
SCRIPTS = STARTER / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import r6_hidden_stress as r6  # noqa: E402


def test_generated_data_variant_has_seeded_nonpublic_entities_and_direct_oracle(tmp_path):
    variant = r6.make_data_variant(tmp_path / "variant", seed=9141, family="values")

    assert variant.db_path.exists()
    assert variant.store_id.startswith("S") and variant.store_id not in {"S01", "S02"}
    assert variant.product_id.startswith("P") and variant.product_id not in {"P01", "P02"}

    with sqlite3.connect(variant.db_path) as conn:
        rows = conn.execute(
            "SELECT order_id, date, qty, amount, store_id, product_id "
            "FROM sales WHERE store_id = ? AND product_id = ? "
            "ORDER BY date, order_id",
            (variant.store_id, variant.product_id),
        ).fetchall()

    expected_amount = sum(float(row[3]) for row in rows)
    expected_qty = sum(int(float(row[2])) for row in rows)
    expected_orders = len({row[0] for row in rows})
    expected = {
        "net_revenue": round(expected_amount, 2),
        "qty": expected_qty,
        "orders": expected_orders,
        "aov": round(expected_amount / expected_orders, 2),
    }

    assert r6.data_oracle(
        variant.db_path,
        start=variant.start,
        end=variant.end,
        store_id=variant.store_id,
        product_id=variant.product_id,
    ) == expected


def test_data_oracle_does_not_call_production_answering_layers(tmp_path, monkeypatch):
    variant = r6.make_data_variant(tmp_path / "variant", seed=9142, family="values")

    from kbqa import planner as planner_module
    from kbqa import service as service_module

    monkeypatch.setattr(
        planner_module.Planner,
        "plan",
        lambda *args, **kwargs: pytest.fail("data oracle called Planner"),
    )
    monkeypatch.setattr(
        service_module.Service,
        "chat",
        lambda *args, **kwargs: pytest.fail("data oracle called Service.chat"),
    )

    result = r6.data_oracle(
        variant.db_path,
        start=variant.start,
        end=variant.end,
        store_id=variant.store_id,
        product_id=variant.product_id,
    )

    assert result["net_revenue"] > 0
    assert result["orders"] >= 1


def test_data_families_are_reproducible_and_change_with_seed(tmp_path):
    first = r6.make_data_variant(tmp_path / "first", seed=9143, family="rows")
    same = r6.make_data_variant(tmp_path / "same", seed=9143, family="rows")
    other = r6.make_data_variant(tmp_path / "other", seed=9144, family="rows")

    assert first.db_path.read_bytes() == same.db_path.read_bytes()
    assert first.db_path.read_bytes() != other.db_path.read_bytes()
    assert first.db_path.parent != other.db_path.parent
