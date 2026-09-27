"""Deterministic semantic follow-up resolution.

This module decides whether the current utterance may consult semantic state
and exposes inherited slots.  It does not choose the final intent/kind/tool;
that remains the Planner's authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from . import entities as E
from .conversation import ConversationState
from .timeparse import TimeSpec, loose_days, parse_time


@dataclass
class Resolution:
    question: str
    inherited: dict[str, Any] = field(default_factory=dict)
    topic_query: str = ""
    continuation: bool = False
    operator: str | None = None
    ambiguous: bool = False


def _topic_anchor(text: str) -> str:
    """Remove temporal scope and interrogative glue from a topic anchor."""
    value = re.sub(r"[？?。，,、：:；;！!]", " ", text or "")
    value = re.sub(
        r"(?:20\d{2}\s*年\s*)?[0-9一二两三四五六七八九十]{1,3}\s*月"
        r"(?:\s*[0-9一二两三四五六七八九十]{1,3}\s*[日号])?",
        " ", value,
    )
    value = re.sub(r"20\d{2}\s*年|[0-9一二两三四五六七八九十]{1,3}\s*[日号]", " ", value)
    for word in (
        "现在", "目前", "当前", "今天", "此刻", "最近", "当时", "那时", "时候",
        "首月", "第一个月", "为什么", "为何", "怎么", "多少", "是不是", "吗", "呢",
        "那", "那么", "接着", "然后", "的", "了", "吧",
    ):
        value = value.replace(word, " ")
    return " ".join(value.split())


def _legacy_state(history: list[dict]) -> ConversationState:
    if not history:
        return ConversationState.empty()
    previous = history[-1] or {}
    slots = dict(previous.get("slots") or {})
    topic = _topic_anchor(previous.get("standalone") or previous.get("question") or "")
    return ConversationState.from_dict({
        **slots,
        "topic_query": slots.get("topic_query") or topic,
        "recent_windows": slots.get("recent_windows") or [],
    })


def _coerce_state(value: ConversationState | list[dict] | None) -> ConversationState:
    if isinstance(value, ConversationState):
        return value
    if isinstance(value, list):
        return _legacy_state(value)
    if isinstance(value, dict):
        return ConversationState.from_dict(value)
    return ConversationState.empty()


class FollowUps:
    """Resolve semantic continuity without reconstructing a prior sentence."""

    def __init__(self, catalog: E.Catalog, today: date) -> None:
        self.catalog = catalog
        self.today = today

    def _is_follow_up(self, question: str, state: ConversationState) -> bool:
        if state.is_empty():
            return False
        own_time = parse_time(question, self.today)
        own_topic = (
            bool(self.catalog.find_store(question)[0])
            or bool(self.catalog.find_product(question)[0])
            or E.has_any(question, E.BUSINESS_WORDS)
            or bool(E.find_metric(question))
        )
        if own_time.explicit and own_topic and not E.looks_like_follow_up(question):
            return False
        if E.looks_like_follow_up(question):
            return True
        text = question.strip()
        if len(text) > 32:
            return False
        return bool(re.search(
            r"(后来|之后|然后|接着|还有|再|又|那次|这次|当时|结果|供应商|实际|各店|两个月|"
            r"两个区间|两段时间|两者|这段|那一周|这一周)", text
        ))

    def resolve(
        self, question: str, state_or_history: ConversationState | list[dict] | None,
    ) -> Resolution:
        state = _coerce_state(state_or_history)
        continuation = self._is_follow_up(question, state)
        if not continuation:
            return Resolution(question=question)

        store, _ = self.catalog.find_store(question)
        product, _ = self.catalog.find_product(question)
        metric = E.find_metric(E.focus_text_for_metric(question))
        operator = None
        if E.has_any(question, ("实际", "实销", "实际卖")):
            operator = "actual"
        elif E.has_any(question, E.STORE_WORDS):
            operator = "dimension_shift"
        elif E.has_any(question, E.WHY_WORDS):
            operator = "reason"
        elif E.has_any(question, ("现在", "目前", "当前")):
            operator = "current"
        elif E.has_any(question, ("当时", "那时候", "那会儿")):
            operator = "historical"
        elif re.search(r"(这|那|前)?\s*(两个月|两个区间|两段时间|两者|两个月份)", question):
            operator = "comparison"

        inherited = {
            "store_id": state.store_id if not store else None,
            "product_id": state.product_id if not product else None,
            "metric": state.metric if not metric else None,
            "window": state.window,
            "compare_window": state.compare_window,
            "recent_windows": list(state.recent_windows),
            "as_of": state.as_of,
            "historical": state.historical,
            "intent": state.intent,
            "kind": state.kind,
            "topic_query": state.topic_query,
            "topic_kind": state.topic_kind,
            "source_anchors": list(state.source_anchors),
        }
        topic_shift = bool(re.search(r"(供应商|赔偿|赔了|断供|停售|事故|通知)", question))
        if topic_shift:
            # Keep the entity/topic anchor for the explanation query, but do not
            # turn an event follow-up into a data query for the old time range.
            inherited["window"] = None
            inherited["compare_window"] = None
            inherited["as_of"] = None
            inherited["historical"] = False
            inherited["topic_shift"] = True
        ambiguous = (
            not store and not product and not metric
            and not parse_time(question, self.today).explicit
            and bool(state.store_id and state.product_id)
            and bool(re.search(r"^(那|它|这个|那个|这家|那家)", question.strip()))
        )
        return Resolution(
            question=question,
            inherited=inherited,
            topic_query=state.topic_query,
            continuation=True,
            operator=operator,
            ambiguous=ambiguous,
        )

    def inherit_time(self, plan, spec: TimeSpec, question: str, inherited: dict) -> None:
        """Fill only missing time from semantic state; never from old text."""
        recent = [tuple(window) for window in (inherited.get("recent_windows") or []) if window]
        topic_shift = bool(re.search(r"(供应商|赔偿|赔了|断供|停售|事故|通知)", question))
        if not spec.windows and not spec.relative_now and not topic_shift:
            days = loose_days(question)
            anchor = recent[-1] if recent else inherited.get("window")
            if days and not anchor:
                plan.slots["needs_month"] = True
                plan.slots["loose_day"] = days[0]
            if days and anchor:
                month = date.fromisoformat(anchor[0])
                points = sorted(
                    date(month.year, month.month, min(day, 28 if month.month == 2 else 31))
                    for day in days
                )
                spec.windows = [(points[0].isoformat(), points[-1].isoformat())]
                spec.labels.append("%d月%s日" % (month.month, "、".join(str(d) for d in days)))
                spec.explicit = True
                spec.as_of = points[-1]
                plan.notes.append("“%s”按语义状态的月份补全为 %s。" % (question.strip(), spec.windows[0]))
            elif anchor:
                spec.windows = [tuple(anchor)]
                plan.notes.append("当前问题未写时间，沿用语义状态窗口 %s。" % (anchor,))
                if inherited.get("as_of"):
                    spec.as_of = date.fromisoformat(str(inherited["as_of"])[:10])
        if re.search(r"(这|那|前)?\s*(两个月|两个区间|两段时间|两者|两个月份)", question) and len(recent) >= 2:
            spec.windows = [recent[-2], recent[-1]]
            spec.explicit = True
            spec.as_of = date.fromisoformat(recent[-1][1])
            plan.notes.append("“这两个月”指语义状态中的 %s 与 %s。" % (recent[-2], recent[-1]))
        if re.search(r"(那一周|这一周|那周|同一周)", question) and recent:
            spec.windows = [recent[-1]]
            spec.explicit = True


def _topic_terms(previous: str) -> str:
    """Compatibility helper; new production resolution uses `_topic_anchor`."""
    return _topic_anchor(previous)

