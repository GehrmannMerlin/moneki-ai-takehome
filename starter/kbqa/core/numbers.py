"""数字语义：把"一段文本里哪些数才是经营数字"这件事收成一处。

背景（Generalization Round 2）：生产代码原先在 ``live.py`` 里用两条粗 regex
（``_NUMBER`` + 只认 ``YYYY-MM-DD`` 的 ``_DATE_LIKE``）判断回答里的数字有没有
依据。真实 trace 里因此出现过::

    KB-001 → -1      KB-029 → -29      07-31 → -31

被当成"模型编造的经营数字"，于是 DeepSeek 的**正确答案**被判定不通过。
根因不是正则写得不够长，而是"数字语义"没有单一权威。

本模块的口径**与评测脚本 ``eval/run_eval.py::extract_numbers`` 对齐**——
遮蔽日期/时间/编号/电话，再取裸数字，并处理 NFKC、千分位、百分号、万/亿。
对齐是**语义对齐**，不是代码依赖：生产不允许 ``from eval.run_eval import ...``，
所以这里独立实现一份；``tests`` 里有一次交叉验证把它钉住。

三处调用点共用同一份语义，避免"日期识别 / ID 识别 / 证据计数 / 事实支持"
再次长成四套互相不一致的判断：

* ``extract_numbers``         —— 契约口径的数字提取（回答卫生、证据数字预算、契约计数）；
* ``numbers_in_object``       —— 从 ``data_evidence`` 的 ``result`` 取数（同口径）；
* ``extract_contract_numbers``—— 语义与 ``extract_numbers`` 相同的显式别名，
  供"契约计数"语境使用，读代码的人一眼能看出这里量的是契约而非主张。

回答事实校验（claim validation）与契约计数用的是**同一个**经营数字定义，
所以这里不再另造 ``extract_claim_numbers``：多一个名字只会多一份漂移的风险。
"""

from __future__ import annotations

import json
import math
import unicodedata

__all__ = [
    "MAX_ABS_NUMBER",
    "nfkc",
    "extract_numbers",
    "extract_contract_numbers",
    "extract_date_parts",
    "numbers_in_object",
]

#: 可比较的数值范围：再大按"不是有效数字"处理（与评测脚本同值）。
MAX_ABS_NUMBER = 1e15

#: 先把"不是答案数字"的东西整段遮掉。**顺序重要**：编号与日期必须先于裸数字被吃掉，
#: 否则 ``KB-001`` 会先被裸数字正则匹配成 ``-1``。
_MASK_PATTERNS = [
    r"KB-\d{3}",                                        # 文档编号
    r"\bORD\d+",                                        # 订单号
    r"\b[A-Za-z][A-Za-z0-9]{0,5}-\d[\dA-Za-z-]*",       # t-2026...-0001、HU-8842
    r"\b[A-Za-z]{1,3}\d{2,}\b",                         # S02、P06、A0117
    r"\d{3,4}-\d{4}-\d{4}",                             # 电话
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}",                     # 2026-06-18、2026/6/5
    r"\d{1,2}[-/]\d{1,2}[-/]\d{4}",                     # 18-06-2026
    r"\d{4}\s*年(?:\s*\d{1,2}\s*月)?(?:\s*\d{1,2}\s*[日号])?",
    r"(?<![\d个半])\d{1,2}\s*月(?:\s*\d{1,2}\s*[日号])?",
    r"(?<![\d个半])\d{1,2}\s*[日号](?![\d])",
    r"\d{1,2}:\d{2}(?::\d{2})?",                        # 23:00
]
_MASK_RE = None
_THOUSANDS_RE = None
_PERCENT_RE = None
_SCALE_RE = None
_NUMBER_RE = None
_SCALES = {"万": 10000, "亿": 100000000}


def _compile():
    """惰性编译（避免在 import 期做正则编译，便于极端环境下延迟报错）。"""
    global _MASK_RE, _THOUSANDS_RE, _PERCENT_RE, _SCALE_RE, _NUMBER_RE
    import re

    if _MASK_RE is None:
        _MASK_RE = re.compile("|".join("(?:%s)" % p for p in _MASK_PATTERNS))
        _THOUSANDS_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")
        _PERCENT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")
        _SCALE_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*([万亿])")
        _NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
    return _MASK_RE, _THOUSANDS_RE, _PERCENT_RE, _SCALE_RE, _NUMBER_RE


def nfkc(text: str) -> str:
    """全角转半角、兼容字符归一（NFKC）。"""
    return unicodedata.normalize("NFKC", text or "")


def _usable(value: float) -> bool:
    return math.isfinite(value) and abs(value) <= MAX_ABS_NUMBER


def extract_numbers(text) -> list[float]:
    """把一段文本里"当作经营数字"的数取出来（契约口径）。

    * 千分位逗号、``¥``、``元`` 都去掉（``¥13,524.00`` / ``13524元`` → 13524.0）；
    * 全角数字先转半角（NFKC）；
    * ``100%`` 与 ``100`` 都得到 100.0；
    * ``万`` / ``亿`` 换算（``1.23万`` → 12300.0）；
    * 日期（``2026-06-18``、``2026年7月31日``、``7月31日``）、时间（``23:00``）、
      文档编号（``KB-013``）、门店商品编号（``S02`` / ``P06``）、订单号、电话
      **都不算经营数字**；
    * 非有限数与绝对值超过 ``MAX_ABS_NUMBER`` 的数一律丢掉。
    """
    if not isinstance(text, str):
        return []
    mask_re, thousands_re, percent_re, scale_re, number_re = _compile()
    s = nfkc(text)
    s = mask_re.sub(" ", s)
    s = thousands_re.sub(lambda m: m.group(0).replace(",", ""), s)
    out: list[float] = []

    def take(raw: str, factor: float = 1.0) -> None:
        try:
            value = float(raw) * factor
        except (ValueError, OverflowError):                  # pragma: no cover
            return
        if _usable(value):
            out.append(value)

    for match in percent_re.finditer(s):
        take(match.group(1))
    s = percent_re.sub(" ", s)
    for match in scale_re.finditer(s):
        take(match.group(1), _SCALES[match.group(2)])
    s = scale_re.sub(" ", s)
    for match in number_re.finditer(s):
        take(match.group(0))
    return out


def extract_contract_numbers(text) -> list[float]:
    """契约计数用的数字提取。

    语义与 :func:`extract_numbers` **完全相同**——契约（``evidence_hygiene`` 的
    数字预算、``number_flood`` 的"不同数字个数"）量的就是经营数字。留着这个
    名字是为了让调用点的意图显式：这里在数契约，而不是在核对回答主张。
    """
    return extract_numbers(text)


def numbers_in_object(obj) -> list[float]:
    """从 ``data_evidence`` 的 ``result`` 这类对象里取数（同口径）。"""
    try:
        blob = json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):                          # pragma: no cover
        blob = str(obj)
    return extract_numbers(blob)


#: 日期型 token：只用来把"问题自己写的日期"拆成年/月/日分量。
#: 用途见 ``live._allowed_numbers``——回答里复述问题日期（"8 月 17 日到 19 日"）
#: 不该被当成编造的经营数字。注意这**不是**在放宽经营数字语义：
#: ``extract_numbers`` 依旧把日期整体遮蔽，两者互不影响。
_DATE_PART_PATTERNS = [
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}",
    r"\d{1,2}[-/]\d{1,2}[-/]\d{4}",
    r"\d{4}\s*年(?:\s*\d{1,2}\s*月)?(?:\s*\d{1,2}\s*[日号])?",
    r"(?<![\d个半])\d{1,2}\s*月(?:\s*\d{1,2}\s*[日号])?",
    r"(?<![\d个半])\d{1,2}\s*[日号](?![\d])",
]


def extract_date_parts(text) -> list[float]:
    """把文本里日期型 token 的年/月/日分量取出来（供白名单放行"复述日期"）。"""
    import re

    if not isinstance(text, str) or not text:
        return []
    parts: list[float] = []
    for pattern in _DATE_PART_PATTERNS:
        for match in re.finditer(pattern, text):
            for chunk in re.findall(r"\d+", match.group(0)):
                value = float(chunk)
                if _usable(value):
                    parts.append(value)
    return parts
