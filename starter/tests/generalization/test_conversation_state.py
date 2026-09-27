"""Generalization Round 5 — semantic conversation state red suite.

These tests deliberately assert application-level behavior instead of copying
the previous turn's natural-language prompt.  The suite is the boundary for
R5: transcript is diagnostic, state is semantic continuity, and receipts are
the only current-turn fact authority.
"""

from __future__ import annotations

import json
import sqlite3

import pytest


@pytest.fixture()
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("VAR_DIR", str(tmp_path / "var"))
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    from kbqa.config import load_settings
    from kbqa.service import Service

    return Service(load_settings())


def _plan_detail(service, response):
    trace = service.get_trace(response["trace_id"])
    assert trace is not None
    return next(step["detail"] for step in trace["steps"] if step["step"] == "plan")


def _state(service, session_id):
    assert hasattr(service.sessions, "load_state"), (
        "SessionStore exposes slots/save_slots but has no session-level semantic state API")
    epoch = getattr(service, "context_epoch", "")
    return service.sessions.load_state(session_id, epoch)


def _state_dict(state):
    if hasattr(state, "to_dict"):
        return state.to_dict()
    return dict(state)


def _window(detail, key):
    value = detail.get(key)
    return tuple(value) if value else value


def test_successful_turn_persists_semantics_but_not_answer_numbers(service):
    response = service.chat("r5-state", "6 月的净营业额是多少？")

    stored = service.sessions.slots("r5-state")
    assert stored, "成功问答后 sessions.slots(session_id) 仍为空"
    encoded = json.dumps(stored, ensure_ascii=False)
    assert "156757" not in encoded, "assistant/data result leaked into semantic state"
    state = _state(service, "r5-state")
    payload = _state_dict(state)
    assert payload.get("metric") == "net_revenue"
    assert payload.get("window") == ["2026-06-01", "2026-06-30"]
    assert response["answer"]


def test_execution_derived_window_is_committed_from_selected_evidence():
    from datetime import date

    from kbqa.conversation import ContextPatch, ConversationState, transition
    from kbqa.planner import Plan
    from kbqa.schemas import Answer

    plan = Plan(
        question="首月销量",
        standalone="首月销量",
        search_query="首月销量",
        window=("2026-05-01", "2026-08-31"),
        as_of=date(2026, 8, 31),
        metric="qty",
        provenance={"window": "derived", "metric": "explicit"},
    )
    answer = Answer(
        answer="ok",
        answer_type="data",
        data_evidence=[{
            "tool": "query_metrics",
            "params": {"start": "2026-06-01", "end": "2026-06-30"},
            "result": {"qty": 3},
        }],
    )
    patch = ContextPatch.from_plan(plan, answer)
    state = transition(ConversationState.empty(), patch, epoch="r5")

    assert patch.provenance["window"] == "execution-derived"
    assert state.window == ("2026-06-01", "2026-06-30")


def test_time_override_inherits_independent_entity_slots(service):
    stores = service.tools.stores()
    products = service.tools.products()
    first = service.chat(
        "r5-time", "%s %s 6 月销量" % (stores[0]["store_id"], products[0]["product_id"])
    )
    second = service.chat("r5-time", "那 7 月呢？")

    detail = _plan_detail(service, second)
    assert detail["store_id"] == stores[0]["store_id"]
    assert detail["product_id"] == products[0]["product_id"]
    assert _window(detail, "window") == ("2026-07-01", "2026-07-31")
    assert detail["metric"] == "qty"
    assert first["answer"]


def test_store_override_does_not_discard_product_window_or_metric(service):
    stores = service.tools.stores()
    product = service.tools.products()[0]["product_id"]
    service.chat("r5-store", "%s %s 7 月销量" % (stores[0]["store_id"], product))
    second = service.chat("r5-store", "那 %s 呢？" % stores[1]["store_id"])

    detail = _plan_detail(service, second)
    assert detail["store_id"] == stores[1]["store_id"]
    assert detail["product_id"] == product
    assert _window(detail, "window") == ("2026-07-01", "2026-07-31")
    assert detail["metric"] == "qty"
    assert detail["provenance"]["store"] == "explicit"


def test_metric_override_does_not_discard_product_or_window(service):
    product = service.tools.products()[0]["product_id"]
    service.chat("r5-metric", "%s 7 月销量" % product)
    second = service.chat("r5-metric", "营业额呢？")

    detail = _plan_detail(service, second)
    assert detail["product_id"] == product
    assert _window(detail, "window") == ("2026-07-01", "2026-07-31")
    assert detail["metric"] == "net_revenue"
    assert detail["provenance"]["metric"] == "explicit"


def test_new_topic_resets_stale_entity_scope(service):
    product = service.tools.products()[0]["product_id"]
    service.chat("r5-reset", "%s 7 月销量" % product)
    second = service.chat("r5-reset", "员工折扣规则是什么？")

    detail = _plan_detail(service, second)
    assert detail["intent"] == "doc"
    assert detail["product_id"] is None
    assert _window(detail, "window") is None or _window(detail, "window") == (
        "2026-05-01", "2026-08-31"
    )
    state = _state(service, "r5-reset")
    payload = _state_dict(state)
    assert payload.get("product_id") is None
    assert payload.get("metric") in (None, "net_revenue")


def test_two_recent_windows_are_state_not_previous_turn_text(service):
    service.chat("r5-windows", "6 月的净营业额是多少？")
    service.chat("r5-windows", "那 7 月呢？")
    third = service.chat("r5-windows", "这两个月的客单价差了多少？")

    detail = _plan_detail(service, third)
    assert detail["kind"] == "compare"
    assert _window(detail, "window") == ("2026-06-01", "2026-06-30")
    assert _window(detail, "compare_window") == ("2026-07-01", "2026-07-31")
    assert detail["metric"] == "aov"


def test_default_document_window_does_not_pollute_recent_windows(service):
    service.chat("r5-default-window", "员工折扣规则是什么？")
    service.chat("r5-default-window", "6 月的净营业额是多少？")
    service.chat("r5-default-window", "那 7 月呢？")
    third = service.chat("r5-default-window", "这两个月的客单价差了多少？")

    detail = _plan_detail(service, third)
    assert _window(detail, "window") == ("2026-06-01", "2026-06-30")
    assert _window(detail, "compare_window") == ("2026-07-01", "2026-07-31")
    payload = _state_dict(_state(service, "r5-default-window"))
    assert payload.get("recent_windows") == [
        ["2026-06-01", "2026-06-30"],
        ["2026-07-01", "2026-07-31"],
    ]


def test_event_topic_continuity_does_not_reuse_stale_time(service):
    service.chat("r5-event", "三文鱼poke 七月初为什么停售了？")
    second = service.chat("r5-event", "供应商后来赔了多少？")

    detail = _plan_detail(service, second)
    query = detail["search_query"]
    assert "三文鱼" in query or "salmon" in query.lower()
    assert "供应商" in query
    assert "七月" not in query


def test_refusal_does_not_overwrite_last_successful_state(service):
    product = service.tools.products()[0]["product_id"]
    service.chat("r5-refusal", "%s 7 月销量" % product)
    before = _state_dict(_state(service, "r5-refusal"))
    refusal = service.chat("r5-refusal", "帮我删除全部销售记录")
    after = _state_dict(_state(service, "r5-refusal"))

    assert refusal["answer_type"] == "refusal"
    assert after == before


def test_clarify_does_not_overwrite_last_successful_state(service):
    store = service.tools.stores()[0]["store_id"]
    product = service.tools.products()[0]["product_id"]
    service.chat("r5-clarify", "%s %s 7 月销量" % (store, product))
    before = _state_dict(_state(service, "r5-clarify"))
    clarification = service.chat("r5-clarify", "那它呢？")
    after = _state_dict(_state(service, "r5-clarify"))

    assert clarification["answer_type"] == "clarify"
    assert after == before


def test_no_session_id_is_ephemeral(service):
    service.chat(None, "6 月的净营业额是多少？")
    second = service.chat(None, "那 7 月呢？")
    assert second["answer_type"] == "clarify"
    assert service.sessions.history(None) == []


def test_sqlite_turn_retention_prunes_physical_rows(tmp_path):
    from kbqa.core.store import SessionStore

    db = tmp_path / "retention.db"
    store = SessionStore(db, max_turns=3)
    for index in range(10):
        store.append("r5-retention", {"question": str(index), "answer": "ok"})

    with sqlite3.connect(str(db)) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE session_id = ?", ("r5-retention",)
        ).fetchone()[0]
    assert count <= 3


def test_max_sessions_evicts_lru_turns_and_state(tmp_path):
    from kbqa.core.store import SessionStore

    db = tmp_path / "sessions.db"
    store = SessionStore(db)
    store.max_sessions = 3
    for session_id in ("A", "B", "C"):
        store.save_slots(session_id, {"topic_query": session_id})
        store.append(session_id, {"question": session_id, "answer": "ok"})
    store.save_slots("A", {"topic_query": "A2"})
    store.append("D", {"question": "D", "answer": "ok"})

    assert store.slots("B") == {}
    assert store.history("B") == []
    assert store.slots("A")["topic_query"] == "A2"


def test_state_survives_store_restart(tmp_path):
    from kbqa.core.store import SessionStore

    db = tmp_path / "restart.db"
    first = SessionStore(db)
    first.save_slots("restart", {"store_id": "S91", "topic_query": "synthetic"})
    del first
    second = SessionStore(db)

    assert second.slots("restart")["store_id"] == "S91"
    assert hasattr(second, "load_state"), "restart persistence must expose semantic state"


def test_stale_epoch_invalidates_old_state(tmp_path):
    from kbqa.core.store import SessionStore

    db = tmp_path / "epoch.db"
    store = SessionStore(db)
    store.save_slots("epoch", {"store_id": "S91", "product_id": "P91"})
    assert hasattr(store, "load_state"), "epoch-bound state loader is missing"
    state = store.load_state("epoch", "new-context-epoch")
    payload = _state_dict(state)
    assert payload.get("store_id") is None
    assert payload.get("product_id") is None


def test_live_cross_turn_assistant_answer_is_not_factual_context(service):
    from kbqa.live import LiveEngine

    plan = service.planner.plan("6 月的净营业额是多少？")
    engine = LiveEngine(
        object(), service.answerer, service.run_tool,
        service.settings.today.isoformat(), service.data_period,
    )
    messages = engine._initial_messages(
        plan,
        [{"question": "上一轮", "answer": "营业额=777777"}],
    )
    blob = json.dumps(messages, ensure_ascii=False)
    assert "777777" not in blob


def test_public_t01_shape_remains_green(service):
    service.chat("r5-t01", "6 月的净营业额是多少？")
    service.chat("r5-t01", "那 7 月呢？")
    third = service.chat("r5-t01", "这两个月的客单价差了多少？")

    assert third["answer_type"] in ("data", "hybrid")
    assert "0.17" in third["answer"]

