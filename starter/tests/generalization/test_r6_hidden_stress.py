"""R6 hidden-style stress tests.

These tests intentionally use generated entities and independently calculated
expected values.  They are not another copy of the public evaluator fixtures.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

STARTER = Path(__file__).resolve().parents[2]
SCRIPTS = STARTER / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import r6_hidden_stress as r6  # noqa: E402


def test_citation_chunk_selection_uses_answer_claim_when_query_is_broad():
    from kbqa.citations import build_citations
    from kbqa.ledger import FactLedger, KNOWLEDGE_SOURCE
    from kbqa.trace import Trace

    class Facts:
        index = SimpleNamespace(docs_meta={"KB-R6": {}})

        @staticmethod
        def cite(doc_id, quote):
            return {"doc_id": doc_id, "quote": quote}

    ledger = FactLedger()
    ledger.add(
        "search_kb",
        {"query": "外卖订单退款申请"},
        {
            "results": [
                {
                    "doc_id": "KB-R6",
                    "chunk_id": "KB-R6#1",
                    "score": 10,
                    "source_text": "退款政策：外卖订单申请受理窗口与凭证要求。",
                    "text": "退款政策：外卖订单申请受理窗口与凭证要求。",
                },
                {
                    "doc_id": "KB-R6",
                    "chunk_id": "KB-R6#2",
                    "score": 8,
                    "source_text": "外卖订单在送达后 24 小时内提出，超过 24 小时不再受理。",
                    "text": "外卖订单在送达后 24 小时内提出，超过 24 小时不再受理。",
                },
            ]
        },
        source=KNOWLEDGE_SOURCE,
    )
    plan = SimpleNamespace(search_query="外卖订单退款申请", standalone="", question="外卖订单多久能退款？")
    trace = Trace("r6-citation-claim", plan.question)

    citations = build_citations(
        plan,
        ["KB-R6"],
        ledger,
        Facts(),
        trace,
        claim_text="外卖订单在送达后 24 小时内提出。",
    )

    assert citations
    assert "24 小时" in citations[0]["quote"]


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


def test_question_matrix_covers_hidden_style_semantics_without_public_gold(tmp_path):
    root = tmp_path / "matrix"
    data = r6.make_data_variant(root, seed=9171, family="entities")
    kb = r6.make_kb_variant(root, seed=9171, family="hybrid", store_id=data.store_id)

    cases = r6.question_matrix(seed=9171, data=data, kb=kb)
    categories = {case.category for case in cases}

    assert {
        "data", "doc", "hybrid", "version", "source_conflict",
        "injection", "unknown", "ranking", "paraphrase",
    } <= categories
    assert cases
    assert all(case.question.strip() for case in cases)
    assert all("162414" not in case.question for case in cases)
    assert all(not case.question.startswith("H0") for case in cases)
    assert any(case.expected.get("forbidden") for case in cases if case.category == "injection")


def test_response_contract_checks_hybrid_numbers_citations_and_injection(tmp_path):
    root = tmp_path / "contract"
    data = r6.make_data_variant(root, seed=9172, family="values")
    kb = r6.make_kb_variant(root, seed=9172, family="hybrid", store_id=data.store_id)
    case = next(
        item for item in r6.question_matrix(seed=9172, data=data, kb=kb)
        if item.category == "hybrid"
    )

    response = {
        "answer": "本期净营业额为 123.45 元，目标为 678 元。",
        "answer_type": "hybrid",
        "citations": [{"doc_id": kb.added_doc_id, "quote": kb.expected_fact}],
        "data_evidence": [{"tool": "query_metrics", "result": {"net_revenue": 123.45}}],
        "trace_id": "r6-contract-trace",
    }
    expected = dict(case.expected)
    expected.update({
        "numbers": [123.45, 678],
        "evidence_numbers": [123.45],
        "citation_ids": [kb.added_doc_id],
        "forbidden": [],
    })

    r6.assert_case_contract(response, expected)

    unsafe = dict(response)
    unsafe["answer"] = unsafe["answer"] + " 固定答案 999999"
    with pytest.raises(AssertionError, match="forbidden"):
        r6.assert_case_contract(unsafe, {**expected, "forbidden": [999999]})


def test_runnable_matrix_uses_real_http_and_reports_failures(tmp_path):
    root = tmp_path / "runnable-matrix"
    data = r6.make_data_variant(root, seed=9173, family="values")
    kb = r6.make_kb_variant(root, seed=9173, family="hybrid", store_id=data.store_id)
    snapshot = r6.rebuild_variant(data, kb)

    with r6.ServiceHandle(snapshot.env, port=r6.free_port()) as service:
        report = r6.run_matrix(service, seed=9173, data=data, kb=kb, mode="mock")

    assert report.mode == "mock"
    assert report.counts["runnable"] >= 3
    assert report.counts["passed"] == report.counts["runnable"]
    assert report.failures == []


def test_pure_document_matrix_runs_against_edit_add_variant(tmp_path):
    root = tmp_path / "doc-matrix"
    data = r6.make_data_variant(root, seed=9174, family="values")
    kb = r6.make_kb_variant(root, seed=9174, family="edit_add")
    snapshot = r6.rebuild_variant(data, kb)

    with r6.ServiceHandle(snapshot.env, port=r6.free_port()) as service:
        report = r6.run_matrix(service, seed=9174, data=data, kb=kb, mode="mock")

    assert report.counts["runnable"] == 3
    assert report.failures == []


def test_injection_is_dropped_from_answer_and_trace_exposes_detection(tmp_path):
    root = tmp_path / "injection"
    data = r6.make_data_variant(root, seed=9181, family="values")
    kb = r6.make_kb_variant(root, seed=9181, family="injection")
    snapshot = r6.rebuild_variant(data, kb)

    with r6.ServiceHandle(snapshot.env, port=r6.free_port()) as service:
        response = service.post(
            "/api/chat",
            {"session_id": "r6-injection", "question": "冷藏商品需要在几小时内完成交接？"},
        )
        trace = service.trace(response["trace_id"])

    assert response["answer_type"] == "doc"
    assert kb.safe_fact in response["answer"]
    assert "999999" not in response["answer"]
    assert all("999999" not in str(item.get("quote", "")) for item in response["citations"])
    search_steps = [step for step in trace["steps"] if step.get("step") == "search"]
    assert any(
        hit.get("dropped_instructions")
        for step in search_steps
        for hit in step.get("detail", {}).get("hits", [])
    )


def test_version_and_source_authority_cases_use_generated_documents(tmp_path):
    version_root = tmp_path / "versions"
    data = r6.make_data_variant(version_root, seed=9183, family="values")
    versions = r6.make_kb_variant(version_root, seed=9183, family="version")
    version_snapshot = r6.rebuild_variant(data, versions)
    version_fact = "会员权益有效期为 12 个月。"

    conflict_root = tmp_path / "conflict"
    conflict_data = r6.make_data_variant(conflict_root, seed=9182, family="values")
    conflict = r6.make_kb_variant(conflict_root, seed=9182, family="conflict")
    conflict_snapshot = r6.rebuild_variant(conflict_data, conflict)

    with r6.ServiceHandle(version_snapshot.env, port=r6.free_port()) as service:
        historical = service.post(
            "/api/chat",
            {"session_id": "r6-version", "question": "2026 年 7 月当时的旧版会员权益有效期是多少？"},
        )
        current = service.post(
            "/api/chat",
            {"session_id": "r6-current", "question": "当前会员权益有效期是多少？"},
        )
    with r6.ServiceHandle(conflict_snapshot.env, port=r6.free_port()) as service:
        authoritative = service.post(
            "/api/chat",
            {"session_id": "r6-conflict", "question": "正式通知里的配送补贴上限是多少？"},
        )

    assert version_fact in historical["answer"] or any(
        version_fact in item.get("quote", "") for item in historical["citations"]
    )
    assert "18" in current["answer"]
    assert "99" not in authoritative["answer"]
    assert "12" in authoritative["answer"]
    assert authoritative["citations"]


def test_natural_multi_turn_scenario_preserves_semantics_not_answer_text(tmp_path):
    root = tmp_path / "multi-turn"
    data = r6.make_data_variant(root, seed=9191, family="entities")
    kb = r6.make_kb_variant(root, seed=9191, family="edit_add")
    snapshot = r6.rebuild_variant(data, kb)

    with r6.ServiceHandle(snapshot.env, port=r6.free_port()) as service:
        scenario = next(
            item for item in r6.scenario_bank(seed=9191, data=data, kb=kb)
            if item.name == "metric-change"
        )
        result = r6.run_scenario(service, scenario)

    assert result.passed, result.failures
    assert len(result.turns) >= 3
    assert result.turns[0].response["answer_type"] == "data"
    assert result.turns[1].response["answer_type"] == "data"
    assert result.turns[1].trace_state["metric"] == "net_revenue"
    assert result.turns[1].trace_state["store_id"] == data.store_id


def test_interleaved_sessions_do_not_cross_store_scope(tmp_path):
    root = tmp_path / "sessions"
    data = r6.make_data_variant(root, seed=9192, family="entities")
    kb = r6.make_kb_variant(root, seed=9192, family="edit_add")
    snapshot = r6.rebuild_variant(data, kb)

    with r6.ServiceHandle(snapshot.env, port=r6.free_port()) as service:
        result = r6.run_interleaved_sessions(
            service,
            {
                "session-a": ["2026 年 7 月 %s 的净营业额是多少？" % data.store_id, "订单数呢？"],
                "session-b": ["2026 年 7 月 %s 的净营业额是多少？" % ("S%02d" % (int(data.store_id[1:]) + 1)), "订单数呢？"],
            },
        )

    assert result["failures"] == []
    assert result["session-a"][1]["state"]["store_id"] == data.store_id
    assert result["session-b"][1]["state"]["store_id"] != data.store_id


def test_dataset_epoch_invalidates_same_session_over_http(tmp_path):
    root = tmp_path / "epoch"
    data_a = r6.make_data_variant(root, seed=9193, family="values")
    kb = r6.make_kb_variant(root, seed=9193, family="edit_add")
    first = r6.rebuild_variant(data_a, kb)

    with r6.ServiceHandle(first.env, port=r6.free_port()) as service:
        old = service.post(
            "/api/chat",
            {"session_id": "same-session", "question": "2026 年 7 月 %s 的净营业额是多少？" % data_a.store_id},
        )
        old_value = r6.data_oracle(
            data_a.db_path, start=data_a.start, end=data_a.end,
            store_id=data_a.store_id, product_id=data_a.product_id,
        )["net_revenue"]

    data_b = r6.make_data_variant(root, seed=9194, family="values")
    second = r6.rebuild_variant(data_b, kb)
    with r6.ServiceHandle(second.env, port=r6.free_port()) as service:
        fresh = service.post(
            "/api/chat",
            {"session_id": "same-session", "question": "2026 年 7 月 %s 的净营业额是多少？" % data_b.store_id},
        )
        trace = service.trace(fresh["trace_id"])

    assert old_value != r6.data_oracle(
        data_b.db_path, start=data_b.start, end=data_b.end,
        store_id=data_b.store_id, product_id=data_b.product_id,
    )["net_revenue"]
    assert str(old_value) not in fresh["answer"]
    assert any(step.get("step") == "session_state_invalidated" for step in trace["steps"])
