"""Structured semantic state for cross-turn conversation continuity.

The transcript records what was said.  This module records only the semantic
slots that the application has resolved for the next turn.  It deliberately
does not model answer text, tool results, or document bodies: those remain
current-turn receipt authorities and must be acquired again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

import re


Window = tuple[str, str]


def _window(value: Any) -> Optional[Window]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        if value[0] is None or value[1] is None:
            return None
        start, end = str(value[0]).strip(), str(value[1]).strip()
        if start and end and start.lower() != "none" and end.lower() != "none":
            return (start, end)
    return None


def _windows(value: Any) -> list[Window]:
    if not isinstance(value, (list, tuple)):
        return []
    return [window for item in value if (window := _window(item))]


def _topic_anchor(text: str) -> str:
    """Keep semantic topic words while removing temporal/query scaffolding."""
    value = re.sub(r"[？?。，,、：:；;！!]", " ", text or "")
    value = re.sub(
        r"(?:20\d{2}\s*年\s*)?[0-9一二两三四五六七八九十]{1,3}\s*月"
        r"(?:\s*[0-9一二两三四五六七八九十]{1,3}\s*[日号])?"
        r"(?:\s*(?:初|中旬?|底))?",
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


@dataclass
class ConversationState:
    """Durable semantic continuity, never a cache of business facts."""

    schema_version: int = 1
    epoch: str = ""
    store_id: Optional[str] = None
    product_id: Optional[str] = None
    metric: Optional[str] = None
    window: Optional[Window] = None
    compare_window: Optional[Window] = None
    recent_windows: list[Window] = field(default_factory=list)
    as_of: Optional[str] = None
    historical: bool = False
    intent: Optional[str] = None
    kind: Optional[str] = None
    topic_query: str = ""
    topic_kind: Optional[str] = None
    source_anchors: list[str] = field(default_factory=list)
    continuation: bool = False
    provenance: dict[str, str] = field(default_factory=dict)
    # Diagnostic-only, never serialized.  Service uses this to trace an epoch
    # invalidation without treating the old state as usable input.
    invalidation_reason: Optional[str] = field(default=None, repr=False, compare=False)

    @classmethod
    def empty(cls, epoch: str = "", reason: Optional[str] = None) -> "ConversationState":
        return cls(epoch=epoch, invalidation_reason=reason)

    @classmethod
    def from_dict(
        cls, payload: Any, *, epoch: Optional[str] = None,
        invalidation_reason: Optional[str] = None,
    ) -> "ConversationState":
        """Normalize state and legacy ``sessions.slots`` payloads safely."""
        if not isinstance(payload, dict):
            return cls.empty(epoch or "", invalidation_reason)
        stored_epoch = str(payload.get("epoch") or "")
        return cls(
            schema_version=int(payload.get("schema_version") or 1),
            epoch=str(epoch if epoch is not None else stored_epoch),
            store_id=payload.get("store_id"),
            product_id=payload.get("product_id"),
            metric=payload.get("metric"),
            window=_window(payload.get("window")),
            compare_window=_window(payload.get("compare_window")),
            recent_windows=_windows(payload.get("recent_windows")),
            as_of=str(payload.get("as_of")) if payload.get("as_of") else None,
            historical=bool(payload.get("historical")),
            intent=payload.get("intent"),
            kind=payload.get("kind"),
            topic_query=str(payload.get("topic_query") or ""),
            topic_kind=payload.get("topic_kind"),
            source_anchors=[str(item) for item in (payload.get("source_anchors") or [])],
            continuation=bool(payload.get("continuation")),
            provenance={str(k): str(v) for k, v in (payload.get("provenance") or {}).items()},
            invalidation_reason=invalidation_reason,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the only representation allowed to cross the storage boundary."""
        return {
            "schema_version": self.schema_version,
            "epoch": self.epoch,
            "store_id": self.store_id,
            "product_id": self.product_id,
            "metric": self.metric,
            "window": list(self.window) if self.window else None,
            "compare_window": list(self.compare_window) if self.compare_window else None,
            "recent_windows": [list(window) for window in self.recent_windows],
            "as_of": self.as_of,
            "historical": self.historical,
            "intent": self.intent,
            "kind": self.kind,
            "topic_query": self.topic_query,
            "topic_kind": self.topic_kind,
            "source_anchors": list(self.source_anchors),
            "continuation": self.continuation,
            "provenance": dict(self.provenance),
        }

    def is_empty(self) -> bool:
        return not any(
            (
                self.store_id, self.product_id, self.metric, self.window,
                self.compare_window, self.recent_windows, self.as_of,
                self.intent, self.kind, self.topic_query, self.source_anchors,
            )
        )


@dataclass
class ContextPatch:
    """Code-owned semantic outcome used to transition session state."""

    store_id: Optional[str] = None
    product_id: Optional[str] = None
    metric: Optional[str] = None
    effective_window: Optional[Window] = None
    compare_window: Optional[Window] = None
    effective_windows: list[Window] = field(default_factory=list)
    as_of: Optional[str] = None
    historical: bool = False
    intent: Optional[str] = None
    kind: Optional[str] = None
    topic_query: str = ""
    topic_kind: Optional[str] = None
    source_anchors: list[str] = field(default_factory=list)
    continuation: bool = False
    provenance: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_plan(cls, plan: Any, answer: Any = None) -> "ContextPatch":
        """Build a patch from Plan and validated code-owned evidence only."""
        provenance = dict(getattr(plan, "provenance", {}) or {})
        effective = _window(getattr(plan, "window", None))
        compare = _window(getattr(plan, "compare_window", None))
        windows: list[Window] = []
        for candidate in (effective, compare):
            if candidate and candidate not in windows:
                windows.append(candidate)
        if answer is not None:
            for evidence in getattr(answer, "data_evidence", []) or []:
                params = evidence.get("params") if isinstance(evidence, dict) else None
                if not isinstance(params, dict):
                    continue
                candidate = _window((params.get("start"), params.get("end")))
                if candidate and candidate not in windows:
                    windows.append(candidate)
                    if effective is None:
                        effective = candidate
                for start_key, end_key in (("start_a", "end_a"), ("start_b", "end_b")):
                    candidate = _window((params.get(start_key), params.get(end_key)))
                    if candidate and candidate not in windows:
                        windows.append(candidate)
        citations = getattr(answer, "citations", []) if answer is not None else []
        anchors = [
            str(item.get("doc_id"))
            for item in citations or []
            if isinstance(item, dict) and item.get("doc_id")
        ]
        current_topic = _topic_anchor(getattr(plan, "question", ""))
        previous_topic = _topic_anchor(getattr(plan, "slots", {}).get("topic_query", ""))
        topic_parts = [part for part in (current_topic, previous_topic) if part]
        return cls(
            store_id=getattr(plan, "store_id", None),
            product_id=getattr(plan, "product_id", None),
            metric=getattr(plan, "metric", None),
            effective_window=effective,
            compare_window=compare,
            effective_windows=windows,
            as_of=(getattr(plan, "as_of", None).isoformat()
                   if getattr(plan, "as_of", None) else None),
            historical=bool(getattr(plan, "slots", {}).get("historical")),
            intent=getattr(plan, "intent", None),
            kind=getattr(plan, "kind", None),
            topic_query=" ".join(dict.fromkeys(topic_parts)),
            topic_kind=getattr(plan, "kind", None),
            source_anchors=anchors,
            continuation=bool(getattr(plan, "continuation", False)),
            provenance=provenance,
        )


def merge_recent_windows(
    previous: Iterable[Window], current: Iterable[Window], *, limit: int = 3,
) -> list[Window]:
    """Append semantic windows deterministically and keep the newest bounded set."""
    merged: list[Window] = []
    for window in list(previous) + list(current):
        normalized = _window(window)
        if normalized and normalized not in merged:
            merged.append(normalized)
    return merged[-max(0, limit):]


def transition(
    previous: ConversationState,
    patch: ContextPatch,
    *,
    epoch: str,
) -> ConversationState:
    """Apply one successful, code-owned turn to semantic session state.

    The answer text and tool receipts are deliberately absent from this
    transition.  A new topic replaces the old semantic scope; a continuation
    uses the already-resolved Plan fields and only carries forward bounded
    recent windows.  Default document scope is not a real user-mentioned
    window and therefore cannot become comparison context.
    """
    window_provenance = patch.provenance.get("window")
    effective_window = patch.effective_window
    if window_provenance == "default":
        effective_window = None

    current_windows = patch.effective_windows
    if window_provenance == "default":
        current_windows = []
    recent = merge_recent_windows(
        previous.recent_windows if patch.continuation else [],
        current_windows,
    )

    return ConversationState(
        schema_version=1,
        epoch=epoch,
        store_id=patch.store_id,
        product_id=patch.product_id,
        metric=patch.metric,
        window=effective_window,
        compare_window=patch.compare_window,
        recent_windows=recent,
        as_of=patch.as_of,
        historical=patch.historical,
        intent=patch.intent,
        kind=patch.kind,
        topic_query=patch.topic_query,
        topic_kind=patch.topic_kind,
        source_anchors=(
            list(patch.source_anchors)
            if patch.source_anchors
            else list(previous.source_anchors) if patch.continuation else []
        ),
        continuation=patch.continuation,
        provenance=dict(patch.provenance),
    )

