"""区间闸与意图复核：把 `core.intent` 的判定落到 `Plan` 上。

## 区间闸（与 metrics API 行为相反）

| 场景 | metrics API | `/api/chat` |
|---|---|---|
| 区间内没有数据 | 返回 0、`aov=null`（契约 §2 明确要求） | — |
| 区间**与数据区间无交集** | 返回 0 | **拒答**，如实说数据范围，**不编数字** |

F01「9 月的营业额是多少」考的就是后者：`answer_type` 必须是 `refusal`，
且 `numbers_none_beyond_question` 不许出现问句里没有的数字。
反过来 M05 考的是前者。两者不矛盾，是同一份数据在两种接口下的不同约定。

## 意图复核

starter 的 `planner` 有个结构性偏差：它把时间解析的结果当作路由依据，
而 `timeparse` 会把"多久""现在"解析成时间窗。于是

* 「外卖订单多久内可以申请退款」→ `data`，窗口 `2026-05-01..08-31`
* 「Super Souper 现在周五晚上营业到几点」→ `refusal`，窗口 `2026-09-01..09-01`

两条都不该那样判。这一层用 `core.intent` 的独立判定覆盖 planner 的 `intent`
（保留它解析出来的实体与窗口——那部分是对的）。
"""

from __future__ import annotations

import re
from typing import Optional

from .intent import Intent

#: 显式的年月日/月/日写法，用来判断"问的是不是区间外的时间"。
_MONTH = re.compile(r"(?:20(\d{2})\s*年\s*)?(\d{1,2})\s*月")
_FULL_DATE = re.compile(r"20\d{2}\s*[-/年]\s*\d{1,2}\s*[-/月]\s*\d{1,2}\s*[日号]?")
_CN_MONTH = re.compile(r"([一二三四五六七八九十]{1,3})\s*月")
_CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_to_int(text: str) -> Optional[int]:
    if text in _CN_DIGITS:
        return _CN_DIGITS[text]
    if text.startswith("十") and len(text) == 2:
        return 10 + _CN_DIGITS.get(text[1], 0)
    if "十" in text:
        head, _, tail = text.partition("十")
        tens = _CN_DIGITS.get(head, 1) if head else 1
        ones = _CN_DIGITS.get(tail, 0) if tail else 0
        return tens * 10 + ones
    return None


def explicit_months(question: str, default_year: int) -> list[tuple[int, int]]:
    """问句里显式写出的 (年, 月)。用于"问的是不是区间外"。"""
    found: list[tuple[int, int]] = []
    if _FULL_DATE.search(question):
        return found                                     # 完整日期交给 planner 的窗口
    for match in _MONTH.finditer(question):
        year = int("20" + match.group(1)) if match.group(1) else default_year
        month = int(match.group(2))
        if 1 <= month <= 12:
            found.append((year, month))
    for match in _CN_MONTH.finditer(question):
        month = _cn_to_int(match.group(1))
        if month and 1 <= month <= 12:
            found.append((default_year, month))
    return sorted(set(found))


def _overlaps(question: str, data_period: dict, today_year: int) -> bool:
    months = explicit_months(question, today_year)
    if not months:
        return True                                      # 没有显式月份：不在本闸判定
    start = data_period.get("start") or ""
    end = data_period.get("end") or ""
    if not start or not end:
        return True
    first = (int(start[:4]), int(start[5:7]))
    last = (int(end[:4]), int(end[5:7]))
    return any(first <= month <= last for month in months)


def off_range(question: str, plan, data_period: dict) -> Optional[str]:
    """问的是数据区间之外的时间吗？返回原因（进 trace），否则 None。"""
    year = plan.as_of.year if getattr(plan, "as_of", None) else 2026
    if _overlaps(question, data_period, year):
        return None
    months = "、".join("%d 年 %d 月" % item for item in explicit_months(question, year))
    return "问句里的时间（%s）与数据区间 %s 至 %s 没有交集" % (
        months, data_period.get("start"), data_period.get("end"))


def apply_intent(plan, intent: Intent, data_period: dict):
    """把意图复核的结果写到 plan 上。

    只覆盖 `intent` / `kind`，**保留 planner 解析出来的实体与窗口**——
    `store_id` / `product_id` / `window` / `as_of` 那些是对的，别动。

    一个要处理的例外：planner 会因为"现在"把纯文档问题判成
    `refusal / out_of_period`（窗口 `today..today` 落在数据区间之外）。
    那不是"问了区间外的时间"，是 planner 把"现在"误当成了时间窗，
    所以这种情况下意图复核有权覆盖它。
    """
    if plan.intent == "clarify":
        return plan
    if plan.intent == "refusal" and plan.kind == "need_context":
        # 追问但没有上文：这是真的没法答，意图复核不该把它掰成能回答的问题
        return plan

    planner_intent = plan.intent

    # `off_range()` 是在**意图复核之前**跑的，它拦的是"问句里确实写了区间外月份"
    # 那一类。这里要处理的是另一半：planner 把"现在"误当成时间窗，
    # 于是 `out_of_period` 变成拒答（"现在周五营业到几点"）。
    #
    # 判据：`plan.standalone` 里**有没有真的写出区间外的月份**。
    # 有 → 是前者（已经在 off_range 拦掉了，走到这里说明没拦，那就别动）；
    # 没有 → 是后者，意图复核有权覆盖。
    if plan.intent == "refusal" and plan.kind == "out_of_period":
        year = plan.as_of.year if getattr(plan, "as_of", None) else 2026
        misread_as_out_of_period = not explicit_months(plan.standalone, year)
    else:
        misread_as_out_of_period = False

    if intent.kind == "doc":
        plan.intent = "doc"
        plan.kind = "doc" if (misread_as_out_of_period
                              or plan.kind != "doc") else plan.kind
        if misread_as_out_of_period:
            plan.slots["window_ignored"] = True
    elif intent.kind == "hybrid":
        if misread_as_out_of_period:
            plan.intent = "hybrid"
            plan.kind = "price" if intent.price_now else "doc"
        elif intent.price_now and plan.kind in ("summary", "doc"):
            plan.intent = "hybrid"
            plan.kind = "price"
        else:
            # 「…为什么比别的周低这么多」这类：数字要查库、原因要查文档。
            # `Answerer.answer()` 的分派是"先看 kind，再看 intent"，
            # 而 `_merge_doc_side` 的触发条件是
            # `plan.intent == "data" and answer_type == "data"`——
            # 所以这里**把 intent 留在 data**、只设 `two_part`，
            # 让数据侧先答出来，再由 `_merge_doc_side` 补文档那一半并提升成 hybrid。
            # 直接设成 hybrid 反而会走 `_answer_doc`，变成纯文档答案（H01/H05/H06 就是这么红的）。
            plan.intent = "data"
            plan.slots["two_part"] = True
    elif misread_as_out_of_period:
        # 意图复核也说是 data，但 planner 的 refusal 来自"现在"这个误判：
        # 放行成数据问题，让槽位继承去补时间窗
        plan.intent = "data"
        plan.kind = "summary"
        plan.slots["window_ignored"] = True

    plan.slots["intent_confidence"] = intent.confidence
    plan.slots["intent_hints"] = intent.hints
    plan.slots["intent_recheck"] = {"planner": planner_intent, "final": plan.intent}
    if intent.why:
        plan.slots["asks_why"] = True
    return plan
