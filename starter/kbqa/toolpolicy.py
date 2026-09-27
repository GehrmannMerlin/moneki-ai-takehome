"""PlanToolPolicy：把模型的工具调用约束在 Planner 已解析的 scope 之内。

它不是第二个 Planner，也不重新规划——它只**执行**已经定稿的 Plan。

## 三条原则（泛化 R3 §24）

1. **显式解析出来的范围不能被静默漂移**：Plan 里 store=S91、window=7/12，
   模型却调 `query_metrics(7/1..7/31, 无门店)` → 不执行，返回结构化冲突。
2. **缺失的参数可以由 Plan 补齐**：Plan 已经知道门店/商品，就不该因为模型省略
   而把范围放大到全店/全期。
3. **冲突的参数不得静默覆盖**：不偷偷把 S92 改成 S91，而是把冲突返回给模型，
   让它自己改。这样现场调试能看出"是模型漂移"，而不是"数据库算错了"。

## 维度展开类工具

`by_store` / `top_products` / `by_store_category` 本身就**在某个维度上展开**：
不能因为 Plan 里有 store 就往 `by_store` 塞 store，也不能因为 Plan 里有 product
就往 `top_products` 塞 product——那会把"哪家最高"变成"这一家是多少"。

策略完全由 **Plan + 工具语义**决定，不看题型、不看具体门店/商品编号。
"""

from __future__ import annotations

from typing import Any, Optional

#: 每个工具支持哪些维度的约束：
#:   "fill"    —— 缺了就按 Plan 补，与 Plan 冲突就拒绝（仅当该维度是"已解析"的）
#:   "enforce" —— 只做冲突检查，不主动补（现价工具只查需要的那段窗口）
#:   False     —— 该工具在这个维度上不受约束（维度展开，或工具根本不支持）
#: `strict` 列出"即使 Plan 没解析出这个维度，也不许模型自己发明"的维度。
TOOL_SCOPE: dict[str, dict[str, Any]] = {
    "query_metrics": {"window": "fill", "store": "fill", "product": "fill"},
    "daily_metrics": {"window": "fill", "store": "fill", "product": "fill"},
    "payment_mix": {"window": "fill", "store": "fill", "product": False},
    "top_products": {"window": "fill", "store": "fill", "product": False},
    "by_store": {"window": "fill", "store": False, "product": "fill"},
    "by_store_category": {"window": "fill", "store": False, "product": False},
    "compare_periods": {"window": "fill", "store": "fill", "product": "fill"},
    "unit_price_check": {"window": "enforce", "store": False, "product": "fill",
                         "strict": ("product",)},
    "first_sale_date": {"window": False, "store": False, "product": "fill",
                        "strict": ("product",)},
    "search_kb": {"window": False, "store": False, "product": False},
}

#: 这些来源说明该槽位是"应用真的解析出来的"，可以据此拒绝模型漂移。
#: `default`（例如没写时间就落到全区间）与 `none` 不算——那是"没解析出来"。
_RESOLVED = ("explicit", "inherited", "derived")


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _meta(status: str, tool: str, proposed: dict, effective: Optional[dict],
          filled: Optional[dict] = None) -> dict:
    return {
        "status": status,
        "tool": tool,
        "proposed": dict(proposed),
        "effective": dict(effective) if effective is not None else None,
        "filled": dict(filled or {}),
    }


def _conflict(tool: str, proposed: dict, field: str, expected, received) -> dict:
    meta = _meta("rejected", tool, proposed, None)
    meta.update({
        "field": field,
        "expected": expected,
        "received": received,
        "error": "tool scope conflicts with resolved plan",
        "message": (
            "工具参数与已经解析出来的问题范围冲突：%s 应当是 %s，而这次调用给的是 %s。"
            "请按已解析的范围重新调用工具。" % (field, expected, received)
        ),
    })
    return meta


def _scope_field(plan, effective: dict, filled: dict, proposed: dict, tool: str,
                 field: str, plan_value: Optional[str], mode: str,
                 strict: bool = False) -> Optional[dict]:
    """门店/商品这类离散槽位：补齐 or 拒绝冲突。"""
    if not plan_value:
        if strict and _clean(effective.get(field)):
            return _conflict(tool, proposed, field, None, effective.get(field))
        return None                                  # Plan 没解析 → 留空（可能是维度展开）
    got = _clean(effective.get(field))
    if got:
        if got.upper() != str(plan_value).strip().upper():
            return _conflict(tool, proposed, field, plan_value, got)
        return None
    effective[field] = plan_value
    filled[field] = plan_value
    return None


def _scope_window(plan, effective: dict, filled: dict, proposed: dict, tool: str,
                  provenance: dict, mode: str) -> Optional[dict]:
    """单段工具（start/end）的时间窗：补齐 or 拒绝扩大的窗口。"""
    window = getattr(plan, "window", None)
    if not window:
        return None
    start, end = window[0], window[1]
    resolved = provenance.get("window") in _RESOLVED
    got_start, got_end = _clean(effective.get("start")), _clean(effective.get("end"))
    if resolved:
        if (got_start and got_start != start) or (got_end and got_end != end):
            return _conflict(tool, proposed, "window", [start, end], [got_start, got_end])
        for key, want in (("start", start), ("end", end)):
            if not _clean(effective.get(key)):
                effective[key] = want
                filled[key] = want
        return None
    if mode == "fill" and not got_start and not got_end:
        effective["start"], effective["end"] = start, end
        filled["start"], filled["end"] = start, end
    return None


def _scope_compare(plan, effective: dict, filled: dict, proposed: dict, tool: str,
                   provenance: dict) -> Optional[dict]:
    """两段比较：A 用 Plan.window，B 用 Plan.compare_window。"""
    window = getattr(plan, "window", None)
    other = getattr(plan, "compare_window", None)
    if not window:
        return None
    resolved = provenance.get("window") in _RESOLVED
    pairs = [("start_a", "end_a", window)]
    if other:
        pairs.append(("start_b", "end_b", other))
    for start_key, end_key, want in pairs:
        got_start = _clean(effective.get(start_key))
        got_end = _clean(effective.get(end_key))
        if resolved:
            if (got_start and got_start != want[0]) or (got_end and got_end != want[1]):
                return _conflict(tool, proposed, "window", list(want),
                                 [got_start, got_end])
            for key, value in ((start_key, want[0]), (end_key, want[1])):
                if not _clean(effective.get(key)):
                    effective[key] = value
                    filled[key] = value
        elif not got_start and not got_end:
            effective[start_key], effective[end_key] = want[0], want[1]
            filled[start_key], filled[end_key] = want[0], want[1]
    return None


class PlanToolPolicy:
    """输入 canonical Plan + 工具名 + 模型给的参数，输出 effective params 或冲突。"""

    def __init__(self, plan) -> None:
        self.plan = plan

    def apply(self, tool: str, params: Optional[dict]) -> tuple[Optional[dict], dict]:
        proposed = dict(params or {})
        scope = TOOL_SCOPE.get(tool)
        if scope is None:
            # 未知工具：交给 run_tool 报"没有这个工具"，这里不做范围判断。
            return proposed, _meta("ok", tool, proposed, proposed)
        if not getattr(self.plan, "needs_data", False):
            # 纯文档 / 澄清 / 拒答的 Plan 不约束取数范围（那些 turn 里数据只是佐证）。
            return proposed, _meta("ok", tool, proposed, proposed)

        effective: dict = dict(proposed)
        filled: dict = {}
        provenance = getattr(self.plan, "provenance", None) or {}
        strict = set(scope.get("strict") or ())

        if scope.get("window"):
            if tool == "compare_periods":
                bad = _scope_compare(self.plan, effective, filled, proposed, tool, provenance)
            else:
                bad = _scope_window(self.plan, effective, filled, proposed, tool,
                                    provenance, scope["window"])
            if bad:
                return None, bad

        if scope.get("store"):
            bad = _scope_field(self.plan, effective, filled, proposed, tool, "store_id",
                               self.plan.store_id, scope["store"], "store" in strict)
            if bad:
                return None, bad

        if scope.get("product"):
            bad = _scope_field(self.plan, effective, filled, proposed, tool, "product_id",
                               self.plan.product_id, scope["product"], "product" in strict)
            if bad:
                return None, bad

        return effective, _meta("filled" if filled else "ok", tool, proposed,
                                effective, filled)
