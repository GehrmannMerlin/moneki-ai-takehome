"""引用溯源（Citation Provenance）：引用必须来自**本轮真的检索到**的那一段原文。

Generalization Round 4 要闭合的第三条链是「引用与检索凭证的绑定」。历史实现
（`LiveEngine._citations`）只做了一件事：模型点名了哪个 `KB-xxx`，就回**整篇文档**
里重新挑一句最相关的。于是有三个漏洞：

1. **未见文档也能被引用**——模型胡诌一个编号，或点名一篇本轮根本没检索到的文档，
   系统照样回索引里把它捞出来给引用；
2. **chunk provenance 断裂**——挑句在整篇文档上做，引用可能落在**没被检索到**的
   chunk 里，"这条引用来自哪次检索"无从回答；
3. **被 sanitize 的指令句回流**——攻击句虽然没进模型上下文，却可能被当成
   "最相关的一句"重新挑出来当引用。

本模块把引用收敛成一条规则：

    引用只能来自本轮 `search_kb` **真正命中**（非 padded）的 chunk，
    是该 chunk 原文里一段连续文字，逐字可核，规范化后 ≤ 400 字，
    且不得是被 sanitize 掉的指令句。

"本轮检索到过哪些 chunk"来自 **Knowledge Receipt**（`FactLedger`），
不是"索引里有什么"——这是 original source ↔ receipt ↔ citation 的强绑定。
"""

from __future__ import annotations

import re
from typing import Optional

from .core.sanitize import is_instruction_like, split_sentences
from .core.textnorm import normalize_doc
from .core.tokenizer import content_tokens

#: 契约 §5：citation.quote 规范化后不超过 400 字，且仍是连续原文。
FACT_QUOTE_MAX = 400

_MD_HEADING = re.compile(r"^\s*#{1,6}\s")
_CN_HEADING = re.compile(
    r"^\s*(?:[一二三四五六七八九十]+[、.．]|（[一二三四五六七八九十]+）|\d+[、.．])\s*\S")


def _clamp_normalized(text: str, limit: int = FACT_QUOTE_MAX) -> str:
    """把 quote 截到"规范化后 ≤ limit"，且仍然是一段**连续原文**。

    `normalize_doc` 会去掉空白与 `*` `` ` `` `|` `#` `>`，所以"截多少原始字符"
    不等于"留多少可见字符"。这里用二分找最长的、规范化后不超限的**原始前缀**，
    前缀天然还是原文的连续子串，逐字校验必过。
    """
    if len(normalize_doc(text)) <= limit:
        return text
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if len(normalize_doc(text[:mid])) <= limit:
            low = mid
        else:
            high = mid - 1
    return text[:low].rstrip()


def _is_heading(sentence: str) -> bool:
    stripped = sentence.strip()
    if _MD_HEADING.match(stripped):
        return True
    return bool(_CN_HEADING.match(stripped)) and len(stripped) <= 40


def select_quote(chunk_text: str, query: str) -> Optional[str]:
    """从 chunk 原文里挑一段能当引用的连续文字。挑不出来返回 None。

    规则（generic，不看具体编号/题目）：
    * **先剔除被 sanitize 的指令句与标题行**——它们不该成为引用；
    * 在剩下的句子里按"和问题的词重合度"排序，其次取更完整（更长）的那句；
    * 最后按契约压到规范化 ≤ 400 字，仍是连续原文。
    """
    query_terms = set(content_tokens(query or ""))
    candidates: list[tuple[int, int, str]] = []
    for sentence in split_sentences(chunk_text or ""):
        if is_instruction_like(sentence):
            continue
        if _is_heading(sentence):
            continue
        normalized = normalize_doc(sentence)
        if len(normalized) < 6:                      # 半截短语（页脚碎片）不算答案
            continue
        overlap = len(query_terms & set(content_tokens(sentence)))
        candidates.append((overlap, len(normalized), sentence))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], -item[1]))
    return _clamp_normalized(candidates[0][2])


def _pick_chunk(items: list[dict], query: str, claim_text: str = "") -> Optional[dict]:
    """挑与问题及回答 claim 最相关的本轮命中 chunk。

    A broad question can retrieve a document whose decisive fact lives in a
    different chunk from the policy heading.  Once the model has produced a
    claim, its meaningful terms are a second, still-local ranking signal; using
    them keeps the citation and final numeric validation bound to the same
    source span without re-opening the whole document.
    """
    if not items:
        return None
    query_terms = set(content_tokens("%s %s" % (query or "", claim_text or "")))

    def score(item: dict):
        text = item.get("source_text") or item.get("text") or ""
        overlap = len(query_terms & set(content_tokens(text)))
        return (-overlap, -float(item.get("score") or 0.0), item.get("chunk_id") or "")

    return sorted(items, key=score)[0]


def build_citations(
    plan,
    doc_ids: list[str],
    ledger,
    facts,
    trace,
    claim_text: str = "",
) -> list[dict]:
    """把模型点名的 doc 编号，收敛成"来自本轮检索凭证"的引用列表。

    `ledger` 是 :class:`kbqa.ledger.FactLedger`；只有它的 **knowledge receipts**
    才是"本轮检索过什么"的权威。命中的文档集合完全由 receipt 决定——
    索引里存在、但本轮没检索到的文档，系统**不会**回头把它捞出来当引用。
    """
    index = facts.index
    query = plan.search_query or plan.standalone or plan.question or ""
    citation_query = "%s %s" % (query, claim_text or "")

    # ① "本轮检索到过哪些 chunk"——来自 Knowledge Receipt。
    receipts = ledger.knowledge_receipts() if hasattr(ledger, "knowledge_receipts") else []
    retrieved: dict[str, list[dict]] = {}
    for receipt in receipts:
        result = receipt.result if isinstance(receipt.result, dict) else {}
        for item in result.get("results") or []:
            if isinstance(item, dict) and item.get("doc_id"):
                retrieved.setdefault(item["doc_id"], []).append(item)

    year = _question_year(plan)
    citations: list[dict] = []
    for doc_id in doc_ids[:3]:
        if doc_id not in retrieved:
            trace.step("citation_rejected",
                       {"doc_id": doc_id, "reason": "not_retrieved_this_turn"})
            continue
        meta = index.docs_meta.get(doc_id, {})
        # D28：按**问题问的年份**过滤——问 2026 的就不引 2025 的同类文档。
        if year and meta.get("title_year") and int(meta["title_year"]) != year:
            trace.step("citation_rejected",
                       {"doc_id": doc_id, "reason": "year_mismatch", "year": year})
            continue
        chunk = _pick_chunk(retrieved[doc_id], query, claim_text)
        if chunk is None:
            trace.step("citation_rejected", {"doc_id": doc_id, "reason": "no_chunk"})
            continue
        # quote 只能取自**这个本轮命中的 chunk 原文**。
        quote = select_quote(
            chunk.get("source_text") or chunk.get("text") or "",
            citation_query,
        )
        if not quote:
            trace.step("citation_rejected",
                       {"doc_id": doc_id, "chunk_id": chunk.get("chunk_id"),
                        "reason": "no_valid_span"})
            continue
        if is_instruction_like(quote):               # 反向护栏：指令句绝不回流
            trace.step("citation_rejected",
                       {"doc_id": doc_id, "chunk_id": chunk.get("chunk_id"),
                        "reason": "instruction_like"})
            continue
        citation = facts.cite(doc_id, quote)          # 逐字校验（与 evaluator 同构）
        if not citation:
            trace.step("citation_rejected",
                       {"doc_id": doc_id, "chunk_id": chunk.get("chunk_id"),
                        "reason": "not_verbatim"})
            continue
        citations.append(citation)
        trace.step("citation_selected",
                   {"doc_id": doc_id, "chunk_id": chunk.get("chunk_id"),
                    "authority": chunk.get("authority"), "quote": quote})
    return citations


def _question_year(plan) -> Optional[int]:
    """问题问的是哪一年：优先取时间窗，其次取问句里显式写出的年份。"""
    window = getattr(plan, "window", None)
    if window:
        try:
            return int(str(window[0])[:4])
        except (ValueError, TypeError, IndexError):
            pass
    for text in (getattr(plan, "standalone", ""), getattr(plan, "question", "")):
        match = re.search(r"(20\d{2})\s*年", text or "")
        if match:
            return int(match.group(1))
    return None


__all__ = ["build_citations", "select_quote", "FACT_QUOTE_MAX"]
