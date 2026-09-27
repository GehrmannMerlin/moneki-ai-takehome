"""来源权威（Source Authority）：一篇文章对"事实"能负多大责任。

Generalization Round 4 的核心命题是「取到了 ≠ 可信」。同一批检索结果里，
不同文档的**权威等级**不一样：

* **policy / notice**：正式制度、通知、调整——它的数字（目标值、赠送额度、
  门槛）是**规定**，可以作为答案；
* **reference**：总表、名录、对照表、FAQ——是索引性资料，通常要"以最新通知为准"；
* **background**：周报、例会纪要、复盘、顾客反馈汇总——数字是**人工估算**，
  只能当背景与解释，**不能为经营数字背书**；
* **dictionary**：别名/口径词典本身不是答案，是被用来做归一化的；
* **unknown**：认不出来时保守处理。

分类只由 **文档自己的元数据**（`type` / `estimates_only`）决定，
不看编号、不看文件名里的具体词、不写死任何公开知识库的取值——换一套
知识库、换一批类型词，分类会跟着文档走。

`numeric_authority` 表达的是"这篇文档能不能为**经营数字**背书"：
数据库才是经营数字的唯一权威，背景/估算类文档一律为 False。
"""

from __future__ import annotations

#: 正式制度/通知类：数字是规定，可以作答。
POLICY_TYPES = frozenset({
    "政策", "通知", "调整", "制度", "规程", "指引", "手册", "规范", "条例", "办法",
})
#: 索引/参考类：资料性，经常被"以最新通知为准"覆盖。
REFERENCE_TYPES = frozenset({
    "参考资料", "总表", "名录", "对照表", "文档", "FAQ", "常见问题", "索引",
})
#: 背景/估算类：数字是人工估的，只能解释不能背书。
BACKGROUND_TYPES = frozenset({
    "周报", "会议纪要", "复盘", "活动复盘", "报告", "反馈", "汇总", "总结",
})
#: 词典类：本身不是答案，用来做别名/口径归一。
DICTIONARY_TYPES = frozenset({"词典", "别名", "术语表", "口径"})

AUTHORITY_POLICY = "policy"
AUTHORITY_NOTICE = "notice"
AUTHORITY_REFERENCE = "reference"
AUTHORITY_BACKGROUND = "background"
AUTHORITY_DICTIONARY = "dictionary"
AUTHORITY_UNKNOWN = "unknown"


def authority_of(meta: dict) -> str:
    """从文档元数据判来源权威等级（generic，不看具体编号）。"""
    if not meta:
        return AUTHORITY_UNKNOWN
    if meta.get("estimates_only"):
        return AUTHORITY_BACKGROUND
    doc_type = str(meta.get("type") or "").strip()
    if doc_type in DICTIONARY_TYPES:
        return AUTHORITY_DICTIONARY
    if doc_type in BACKGROUND_TYPES:
        return AUTHORITY_BACKGROUND
    if doc_type in REFERENCE_TYPES:
        return AUTHORITY_REFERENCE
    if doc_type in POLICY_TYPES:
        return AUTHORITY_NOTICE if doc_type in ("通知", "调整") else AUTHORITY_POLICY
    # 没有 type 时退一步看文件名/标题里的线索，仍然不依赖具体编号。
    title = str(meta.get("title") or "")
    if any(word in title for word in ("周报", "纪要", "复盘", "反馈汇总")):
        return AUTHORITY_BACKGROUND
    return AUTHORITY_UNKNOWN


def numeric_authority_of(meta: dict) -> bool:
    """这篇文档能否为**经营数字**背书。数据库才是权威，估算/背景类一律不能。"""
    return authority_of(meta) in (AUTHORITY_POLICY, AUTHORITY_NOTICE)


__all__ = [
    "authority_of", "numeric_authority_of",
    "AUTHORITY_POLICY", "AUTHORITY_NOTICE", "AUTHORITY_REFERENCE",
    "AUTHORITY_BACKGROUND", "AUTHORITY_DICTIONARY", "AUTHORITY_UNKNOWN",
]
