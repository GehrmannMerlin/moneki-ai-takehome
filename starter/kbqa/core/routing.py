"""区间闸：判断问题里的时间是不是落在数据区间之外。

## 区间闸（与 metrics API 行为相反）

| 场景 | metrics API | `/api/chat` |
|---|---|---|
| 区间内没有数据 | 返回 0、`aov=null`（契约 §2 明确要求） | — |
| 区间**与数据区间无交集** | 返回 0 | **拒答**，如实说数据范围，**不编数字** |

F01「9 月的营业额是多少」考的就是后者：`answer_type` 必须是 `refusal`，
且 `numbers_none_over_question` 不许出现问句里没有的数字。
反过来 M05 考的是前者。两者不矛盾，是同一份数据在两种接口下的不同约定。

## 这一层现在的归属（泛化 R3）

`off_range()` / `explicit_months()` 现在只作为 **Planner 内部的纯函数**被调用
（`planner._period_gate`）。原先与之相邻的 `apply_intent()`——在 Planner 返回之后
再跑一次意图分类去改写 `intent`/`kind`——已经删除：它构成了第二套规划权威，
现在那份判定收进了 `planner._reconcile_intent`。
"""

from __future__ import annotations

import re
from typing import Optional

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

