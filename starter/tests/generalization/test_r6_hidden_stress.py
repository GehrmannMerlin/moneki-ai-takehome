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


def test_kb_document_oracle_reads_mutated_source_and_contiguous_quote(tmp_path):
    variant = r6.make_kb_variant(tmp_path / "kb-variant", seed=9151, family="edit_add")

    assert variant.added_doc_id not in {"KB-001", "KB-013", "KB-060"}
    assert variant.edited_doc_id
    assert variant.expected_fact
    assert variant.kb_dir.exists()

    result = r6.document_oracle(
        variant.kb_dir,
        doc_id=variant.added_doc_id,
        expected_fact=variant.expected_fact,
    )

    assert result["doc_id"] == variant.added_doc_id
    assert variant.expected_fact in result["visible_text"]
    assert variant.expected_fact in result["quote"]
    assert result["raw_contains_attack"] is False
    assert len("".join(result["quote"].split())) <= 400


def test_kb_families_are_seeded_and_preserve_injection_metadata(tmp_path):
    versions = r6.make_kb_variant(tmp_path / "versions", seed=9152, family="version")
    formats = r6.make_kb_variant(tmp_path / "formats", seed=9153, family="formats")
    injection = r6.make_kb_variant(tmp_path / "injection", seed=9154, family="injection")

    assert versions.version_doc_ids == [
        versions.version_doc_ids[0],
        versions.version_doc_ids[1],
        versions.version_doc_ids[2],
    ]
    assert all(doc_id.startswith("KB-") for doc_id in versions.version_doc_ids)
    assert {path.suffix.lower() for path in formats.kb_dir.rglob("*") if path.is_file()} >= {
        ".md", ".txt", ".html"
    }
    assert injection.raw_attack
    assert injection.safe_fact
    assert injection.raw_attack in injection.attack_source.read_text(encoding="utf-8")


def test_new_document_reaches_rebuilt_http_retrieve_chat_and_trace(tmp_path):
    root = tmp_path / "e2e"
    data = r6.make_data_variant(root, seed=9161, family="values")
    kb = r6.make_kb_variant(root, seed=9161, family="edit_add")

    snapshot = r6.rebuild_variant(data, kb)
    with r6.ServiceHandle(snapshot.env, port=r6.free_port()) as service:
        health = service.get("/api/health")
        assert health["kb_docs"] == 2
        assert health["index_key"]

        retrieved = service.post(
            "/api/retrieve",
            {"query": "配送打包费", "top_k": 5},
        )
        assert any(item["doc_id"] == kb.added_doc_id for item in retrieved["results"])

        answer = service.post(
            "/api/chat",
            {"session_id": "r6-new-doc", "question": "这份新通知的配送打包费是多少？"},
        )
        assert kb.expected_fact.split("为 ", 1)[1].rstrip("。") in answer["answer"]
        assert answer["answer_type"] == "doc"
        assert any(item["doc_id"] == kb.added_doc_id for item in answer["citations"])

        trace = service.trace(answer["trace_id"])
        names = [step.get("step") for step in trace.get("steps", [])]
        assert "plan" in names
        assert "search" in names
        assert "response" in names
