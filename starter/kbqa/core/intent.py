"""意图分类：规则优先，置信度不足时交给 LLM 复核。

## 这一层要解决的真正问题

starter 的 `planner` 几乎把所有问题都路由成"查某个区间的经营数字"：
它把"多久""现在"这类词交给 `timeparse`，而 `timeparse` 会把它们解析成时间窗
（"多久"→整个数据区间，"现在"→`today..today`）。实测：

| 问题 | starter 的判定 | 应该是 |
|---|---|---|
| 外卖订单多久内可以申请退款？ | `data`，窗口 2026-05-01..08-31 | **doc**（KB-013） |
| 员工迟到多久算一次？ | `data`，同上 | **doc**（KB-016） |
| Super Souper 现在周五晚上营业到几点？ | `refusal`，窗口 2026-09-01..09-01 | **doc**（KB-062） |
| 会员现在单笔充值满 500 送多少？ | `refusal`，同上 | **doc**（KB-011） |

**这就是 `doc` 类 16 分全灭的根因**——不是检索找不到答案，是问题压根没往
文档那条路上走。所以判定必须建立在"问句里有没有一个**具体的数据窗口**"上，
而不是"解析器有没有吐出窗口"。

## 判据

一个问题是**数据问题**，当且仅当它同时满足：

1. 问的是经营指标（营业额/订单/销量/客单价/退款/支付/排行/品类）；
2. 句子里有一个**具体的时间指代**（`6 月`、`6 月 8 日`、`最近`、`6 月 8 日到 6 月 14 日`…）。

`多久` / `多长时间` / `几点` / `什么时候` 是**问时长或时点**，不是时间窗——
这一条单独拎出来，因为它是误判的主要来源。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

#: 问"时长/时点/日期"的词：它们出现**不代表**在问某个区间的数据。
DURATION_CLOCK = (
    "多久", "多长时间", "多少天", "几天内", "几小时内", "多少小时",
    "几点", "什么时候", "哪天", "几号", "到几号", "多长时间内",
    "营业到", "营业时间", "开几点", "关几点", "几点到几点", "送到几点",
    "多久内", "几天", "多少分钟", "几分钟",
)


def find_metric(text: str) -> Optional[str]:
    """问句里显式出现的指标词；没有就返回 None。

    **不要拿 `Plan.metric` 当输入**——它的默认值是 `net_revenue`，
    无条件传进来等于对分类器谎称"这题问的是净营业额"。
    """
    from ..entities import find_metric as _find

    return _find(text or "")

#: 制度/规定/流程类词：问这些的一律走文档。
POLICY_HINTS = (
    "政策", "制度", "规定", "怎么算", "怎么计算", "怎么处理", "口径", "算不算",
    "计入", "流程", "标准", "要求", "规定是", "可不可以", "能不能", "允许",
    "怎么办", "怎么跟", "怎么答", "怎么回", "话术", "怎么开", "howto",
    # 「送多少」「赠送」「优惠」问的是活动条款，不是这段时间的经营数字
    "送多少", "赠送", "优惠", "规则", "充值满", "满减", "折扣", "活动价",
)

#: 具体的月份/日期指代。注意顺序：先长后短，避免"6 月 8 日"被"6 月"先吃掉。
#:
#: **不包含"现在/目前/当前"。** 这几个词指的是"当前的状态"而不是一段时间窗：
#: 「会员**现在**单笔充值满 500 送多少」问的是现行条款（KB-011），
#: 「牛肉poke **现在**多少钱」问的是挂牌价（KB-025），都不是"今天这个区间的经营数字"。
#: 把它们排除之后，这类问题会落到"没有具体时间窗"分支 → 正确走文档。
#: 而「**现在**的营业额是多少」仍然会因为命中指标词而判成数据问题（置信度 0.5）。
_TIME_HINTS = (
    re.compile(r"20\d{2}\s*[-/年]\s*\d{1,2}\s*[-/月]\s*\d{1,2}\s*[日号]?"),
    re.compile(r"\d{1,2}\s*月\s*\d{1,2}\s*[日号]"),
    re.compile(r"\d{1,2}\s*月(?:份)?"),
    re.compile(r"第\s*[一二三四五六七八九十\d]+\s*周"),
    re.compile(r"(最近|近期|这段时间|这个月|上个月|下个月|本月|上月|整体|总体|累计|至今|目前为止)"),
    re.compile(r"[一二三四五六七八九十]{1,3}\s*月"),
)

#: 问"现在/目前多少钱"这类**现行售价**的：答案在调价通知里，不在销售表里。
#:
#: 要卡得准。第一版写成 `(现在|目前|当前|如今).{0,6}(多少钱|…|送多少|赠送|规则|优惠)`，
#: 结果「储值充值**现在**的**赠送规则**是什么」也被当成问售价，
#: 跑去查调价通知，答出"知识库里没有该商品的调价通知"。
#: 判别点：**问价钱**（多少钱/什么价/价格/售价/单价/现价/涨价）才走这条；
#: 问"送多少/赠送/优惠/规则"是问活动条款，属于文档问题。
CURRENT_PRICE = re.compile(
    r"(现在|目前|当前|如今).{0,8}(多少钱|什么价|什么价格|价格是多少|售价|单价|现价|涨价|调价)"
    r"|(多少钱|什么价|售价|单价|现价).{0,6}(一份|一个|一杯|现在|目前)")


@dataclass
class Intent:
    kind: str = "data"
    """data / doc / hybrid / refusal / clarify。"""
    confidence: float = 0.6
    metric: str = "net_revenue"
    why: bool = False
    """「为什么」类：检索覆盖率低时禁止强行引用（H06 cite_max=0）。"""
    needs_clock: bool = False
    """问的是时长/时点/日期这类**非数值**信息。"""
    price_now: bool = False
    """问商品现行售价：以最新调价通知为准（KB-001 §5.3 / H04）。"""
    hints: list[str] = field(default_factory=list)


def has_time_reference(text: str) -> bool:
    """句子里有没有一个**具体的时间指代**。

    "多久""几点"不算——它们是问时长与时点，不是指向某个区间。
    """
    return any(pattern.search(text) for pattern in _TIME_HINTS)


def looks_like_duration_or_clock(text: str) -> bool:
    return any(word in text for word in DURATION_CLOCK)


def classify(question: str, metric_word: Optional[str] = None) -> Intent:
    """给一个问题定意图。规则优先；`confidence < 0.6` 时 live 模式交给 LLM 复核。"""
    from ..entities import (
        asks_about_names,
        focus_kinds,
        has_any,
        is_abnormal,
        TARGET_WORDS,
        WHY_WORDS,
    )
    from .tokenizer import normalise
    from ..entities import find_metric

    text = (question or "").strip()
    lowered = normalise(text)
    intent = Intent()
    hints: list[str] = []

    if not text:
        return Intent(kind="clarify", confidence=0.9, hints=["空问题"])

    metric = metric_word or find_metric(lowered)
    if metric:
        intent.metric = metric

    duration_or_clock = looks_like_duration_or_clock(text)
    has_time = has_time_reference(text)
    intent.needs_clock = duration_or_clock and not has_time
    intent.why = has_any(text, WHY_WORDS) or is_abnormal(text)
    intent.price_now = bool(CURRENT_PRICE.search(text))

    policy = has_any(text, POLICY_HINTS)
    focus = set(focus_kinds(text))
    names = asks_about_names(text)
    target = has_any(text, TARGET_WORDS)
    asks_why = has_any(text, WHY_WORDS)

    # ① 问"现在多少钱"这类现行售价：以最新调价通知为准（KB-001 §5.3 / H04/T03）。
    #    走 hybrid 是因为还要用实收单价佐证"建档价滞后"，不是纯文档问题。
    if intent.price_now:
        intent.kind, intent.confidence = "hybrid", 0.75
        hints.append("现行售价以调价通知为准，用实收单价佐证建档价是否滞后")
        return _finish(intent, hints)

    # ② "为什么…低/没有" 这类：原因要查文档，但**数字本身要查库**。
    #    H01/H05/H06 都是这个形状，starter 把它们判成纯文档，于是
    #    `answer_type_in` 与 `evidence_required` 一起红。
    #    只要问句里点了指标词（"营业额/销量/支付"），就按 hybrid 处理。
    if (asks_why or intent.why) and metric and (has_time or intent.needs_clock):
        intent.kind, intent.confidence = "hybrid", 0.75
        hints.append("问原因：数据事实 + 文档解释两条都要")
        return _finish(intent, hints)

    # ③ 达标类（"达到目标了吗"）：目标写在活动方案里，实际值在库里 → hybrid
    if target and (metric or has_time):
        intent.kind, intent.confidence = "hybrid", 0.75
        hints.append("达标判断要对活动方案与实际销量")
        return _finish(intent, hints)

    # ④ 有具体时间窗 + 问指标 → 数据问题（带"为什么"的已在 ② 处理）
    if has_time and metric:
        if policy and not target:
            # 「退款在净营业额里是怎么算的」：有时间词也有指标词，但问的是口径
            intent.kind, intent.confidence = "doc", 0.7
            hints.append("问的是口径而不是这个区间的数值")
            return _finish(intent, hints)
        intent.kind = "hybrid" if target else "data"
        intent.confidence = 0.85
        return _finish(intent, hints)

    # ⑤ 没有具体时间窗
    if duration_or_clock or policy or names or "rule" in focus or "clock" in focus:
        # 问时长/时点/规定/别名 → 文档
        intent.kind, intent.confidence = "doc", 0.8
        return _finish(intent, hints)

    if metric:
        # 有指标没时间：靠槽位继承或反问
        intent.kind, intent.confidence = "data", 0.5
        hints.append("有指标词但没有时间指代，需要继承或反问")
        return _finish(intent, hints)

    # ⑥ 说不清：低置信度，live 时交 LLM 复核
    intent.kind, intent.confidence = "doc", 0.4
    hints.append("既没有指标词也没有明确的时间窗，先按文档试")
    return _finish(intent, hints)


def _finish(intent: Intent, hints: list[str]) -> Intent:
    intent.hints = hints
    return intent
