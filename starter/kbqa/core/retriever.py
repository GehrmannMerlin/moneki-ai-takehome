"""检索：打分、按元数据过滤、先滤后取 top-k。

打分框架（`_eligible` / `_multiplier` / `_concept_scores` / `_history_factor` /
`coverage`）沿用 starter `retriever.py`——那些逻辑是对的，别重写。本文件修两处：

* **D13 先滤后取**：starter 是 `allowed = set(range(len(chunks)))`（全量），
  排序截取到 top_k **之后**才 `hits = [hit for hit in hits if hit.doc_id not in excluded]`。
  契约 §4 明令禁止这个顺序。现在改成先算出合格 chunk 集合，
  只在合格集合里打分、排序、截取。
* **D12 doc_id 归位**：删掉 starter `retriever.py:276` 那行
  `hit.doc_id = ordered[len(hits)].doc_id`——它用"第几条命中"当下标去取
  "排在第 n 位的 chunk 所属文档"，两个下标毫无关系。`Hit.doc_id` 现在恒等于
  `chunk.doc_id`。

**另外修一处原清单没有的缺陷**：`_eligible()` 读 `meta.get("status")`，
而 `Document.meta()` 写的是 `"state"` 键——版本过滤整条失效。
`core/loader.py` 现在两个键都提供。

新增两项：

* `NOTICE_BOOST`：通知/政策类文档对总表/参考类文档加权。KB-042（营业时间总表）
  自己就写了"临时调整以通知为准"，所以 KB-062（8/15 起周五六延至 23:00）
  必须压过 KB-042（21:30）。这是 C03/R03 的判据。
* `Hit.dropped_instructions`：进 LLM 上下文前对 `hit.text` 跑一遍指令句识别，
  被剥的句子记下来。**检索打分与 quote 仍用原文**——
  评测的逐字校验对的是原文，改了就对不上。KB-060 第 42 行是实弹考点。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from .index import BM25Index, load_index
from .sanitize import sanitize
from .tokenizer import content_tokens, tokenize

#: 别名扩展词的权重。**1.0 而不是 starter 的 0.6**：
#: 别名表说"这两种写法是同一个东西"，所以一次别名命中和一次字面命中
#: 应当等价。降到 0.6 的后果实测很直接——R04「三文鱼那次断供供应商赔了多少钱」
#: 问的是英文邮件 KB-022，而中文通知 KB-021 因为字面命中更多词排到了前面，
#: KB-022 掉出 top-5（14/15）。取 1.0 后 15/15。
ALIAS_WEIGHT = 1.0
#: 单字（"月""日""店"）在分词世界里基本是噪声，降权但不丢弃。
SINGLE_CHAR_WEIGHT = 0.3
YEAR_PENALTY = 0.25
FUTURE_PENALTY = 0.6
STORE_HINT_BOOST = 1.15
#: 问某个时间窗里"出了什么事"时，正好在这个窗里生效的文档最可能是答案。
WINDOW_BOOST = 1.8
#: 文档级先验：一篇文档整体命中得好，它的其它片段也更可能是答案所在。
#: 英文邮件里"赔了多少钱"的那一段本身不含任何中文查询词，靠的就是这一项。
DOC_PRIOR = 0.35
#: 别名词典本身不是答案，得压一压，不然它永远排第一。
ALIAS_DOC_PENALTY = 0.5
#: 周报、纪要里的数字是人工估的，问数字的时候给它们降点权。
ESTIMATE_DOC_PENALTY = 0.7
#: **通知/调整类优先于总表/参考类**：KB-042 自己声明"临时调整以通知为准"。
NOTICE_BOOST = 1.2
#: 总表/参考资料在有通知竞争时降权（对标 KB-042 vs KB-062）。
REFERENCE_PENALTY = 0.7
#: top-k 里一篇文档最多占一格：多留几篇不同的文档，比同一篇留两段有用。
MAX_CHUNKS_PER_DOC = 1

#: 这些 `doc_type` 视为"通知/调整类"。
NOTICE_TYPES = frozenset({"通知", "政策", "调整"})
#: 这些 `doc_type` 视为"总表/参考类"。
REFERENCE_TYPES = frozenset({"参考资料", "总表", "名录", "对照表", "文档", "FAQ"})


@dataclass
class Hit:
    doc_id: str
    chunk_id: str
    score: float
    text: str
    source_text: str
    meta: dict
    kind: str = "text"
    table_header: list[str] = field(default_factory=list)
    dropped_instructions: list[str] = field(default_factory=list)
    padded: bool = False
    """凑数补上的：契约 §4 要求恰好返回 top_k 条，但问答链路不会用它作答。"""

    def as_result(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "chunk_id": self.chunk_id,
            "score": round(self.score, 4),
            "text": self.text,
        }

    @property
    def safe_text(self) -> str:
        """进 LLM 上下文用这个（指令句已剥）。**quote 要用 `text`**。"""
        return sanitize(self.text)[0]


@dataclass
class SearchResult:
    hits: list[Hit]
    query: str
    terms: list[str]
    expansions: list[str]
    filtered: list[dict]
    coverage: float = 0.0

    @property
    def ranked(self) -> list[Hit]:
        """真正命中的片段（不含为了凑满 top_k 补上的那些）。"""
        return [hit for hit in self.hits if not hit.padded]

    def as_trace(self) -> dict:
        return {
            "query": self.query,
            "expansions": self.expansions,
            "coverage": round(self.coverage, 3),
            "hits": [
                {
                    "doc_id": hit.doc_id,
                    "chunk_id": hit.chunk_id,
                    "score": round(hit.score, 4),
                    "padded": hit.padded,
                    "dropped_instructions": hit.dropped_instructions,
                }
                for hit in self.hits
            ],
            "filtered": self.filtered,
        }


class Retriever:
    def __init__(self, index: BM25Index, today: date) -> None:
        self.index = index
        self.today = today
        self._effective_to: dict[str, Optional[str]] = {}
        self._in_chain: set[str] = set()
        for doc_id, meta in index.docs_meta.items():
            successor = meta.get("superseded_by")
            if successor and successor in index.docs_meta:
                self._effective_to[doc_id] = index.docs_meta[successor].get("effective_from")
                self._in_chain.add(doc_id)
                self._in_chain.add(successor)
        self._token_cache: dict[int, list[str]] = {}
        #: 元数据过滤的结果缓存：只依赖 (as_of, store_id, historical)，与查询无关，
        #: 所以整个服务生命周期里算一次就够。
        self._eligible_cache: dict[tuple, tuple[set[int], list[dict]]] = {}

    # -- 元数据过滤 -------------------------------------------------------------

    def _status_of(self, meta: dict) -> str:
        """读状态。`status` 是权威键，`state` 是 starter 的旧键名，两个都认。"""
        return str(meta.get("status") or meta.get("state") or "现行")

    def _eligible(
        self, doc_id: str, as_of: date, store_id: Optional[str], historical: bool = False
    ) -> Optional[str]:
        """返回排除原因；返回 None 表示这篇文档可以进入打分。

        问的就是"以前那一版"时（`historical`），不再按生效时间过滤：
        否则已废止的文档永远取不回来，而它恰恰是答案。
        """
        meta = self.index.docs_meta.get(doc_id, {})
        if store_id and meta.get("stores_explicit") and store_id not in (meta.get("stores") or []):
            return "文档声明只适用于 %s，与问题里的 %s 不符" % (
                ",".join(meta.get("stores") or []), store_id)
        if historical:
            return None
        status = self._status_of(meta)
        ends = self._effective_to.get(doc_id)
        # 只有标了"已废止"的才按取代关系挡掉；归档 ≠ 废止，历史周报仍是资料。
        if status == "已废止" and ends and as_of.isoformat() >= ends:
            return "该版本自 %s 起已被 %s 取代" % (ends, meta.get("superseded_by"))
        starts = meta.get("effective_from")
        if starts and starts > as_of.isoformat() and doc_id in self._in_chain:
            return "该版本自 %s 起才生效，晚于问题所指的 %s" % (starts, as_of.isoformat())
        return None

    def _allowed(
        self, as_of: date, store_id: Optional[str], historical: bool
    ) -> tuple[set[int], list[dict]]:
        """算出"合格 chunk 集合"与过滤明细。**这是 D13 的关键**：

        starter 是先取满 top_k 再按 `excluded` 过滤，空位没人补；
        这里把过滤提到打分之前，`allowed` 只含合格文档的 chunk。
        """
        key = (as_of.isoformat(), store_id, bool(historical))
        cached = self._eligible_cache.get(key)
        if cached is not None:
            return cached
        allowed: set[int] = set()
        filtered: list[dict] = []
        for position, chunk in enumerate(self.index.chunks):
            reason = self._eligible(chunk.doc_id, as_of, store_id, historical)
            if reason:
                filtered.append({"doc_id": chunk.doc_id, "chunk_id": chunk.chunk_id,
                                 "reason": reason})
                continue
            allowed.add(position)
        # filtered 按文档去重（同一篇的每个 chunk 都记一条会很长）
        deduped: list[dict] = []
        seen: set[str] = set()
        for item in filtered:
            if item["doc_id"] in seen:
                continue
            seen.add(item["doc_id"])
            deduped.append({"doc_id": item["doc_id"], "reason": item["reason"]})
        result = (allowed, deduped)
        self._eligible_cache[key] = result
        return result

    def _multiplier(
        self,
        doc_id: str,
        as_of: date,
        store_id: Optional[str],
        year: Optional[int],
        window: Optional[tuple[str, str]],
        numeric: bool = False,
    ) -> float:
        meta = self.index.docs_meta.get(doc_id, {})
        factor = 1.0
        title_year = meta.get("title_year")
        if year and title_year and int(title_year) != int(year):
            factor *= YEAR_PENALTY
        starts = meta.get("effective_from")
        if starts and starts > as_of.isoformat():
            factor *= FUTURE_PENALTY
        if store_id and store_id in (meta.get("stores") or []):
            factor *= STORE_HINT_BOOST
        if window and starts and window[0] <= starts <= window[1]:
            factor *= WINDOW_BOOST
        if doc_id == self.index.aliases.source_doc:
            factor *= ALIAS_DOC_PENALTY
        if numeric and meta.get("estimates_only"):
            factor *= ESTIMATE_DOC_PENALTY
        # 通知/调整类优先于总表/参考类（KB-042 自己声明"临时调整以通知为准"）
        doc_type = str(meta.get("type") or "").strip()
        if doc_type in NOTICE_TYPES:
            factor *= NOTICE_BOOST
        elif doc_type in REFERENCE_TYPES:
            factor *= REFERENCE_PENALTY
        return factor

    # -- 检索 -------------------------------------------------------------------

    def _weights(self, query: str) -> dict[str, float]:
        weights: dict[str, float] = {}
        for token in tokenize(query):
            weight = SINGLE_CHAR_WEIGHT if len(token) == 1 else 1.0
            weights[token] = weights.get(token, 0.0) + weight
        return weights

    def _concept_scores(
        self, query: str, allowed: set[int]
    ) -> tuple[dict[int, float], list[str]]:
        """别名按"同一个东西"合并：一个概念只算它最像的那一种写法，不叠加。

        不这么做的话，同时列出全部写法的别名词典自己会永远排第一。
        """
        merged: dict[int, float] = {}
        expansions: list[str] = []
        for canonical in self.index.aliases.mentions(query) + self._store_concepts(query):
            variants = self.index.aliases.variants(canonical)
            best: dict[int, float] = {}
            for variant in variants:
                weights = {token: ALIAS_WEIGHT for token in tokenize(variant)}
                if not weights:
                    continue
                for position, score in self.index.score_terms(weights, allowed).items():
                    if score > best.get(position, 0.0):
                        best[position] = score
            for position, score in best.items():
                merged[position] = merged.get(position, 0.0) + score
            expansions.extend(variants)
        return merged, expansions

    def _history_factor(self, doc_id: str, historical: Optional[bool]) -> float:
        """问旧口径时，已废止的那一版才是答案，给它加权。"""
        if not historical:
            return 1.0
        meta = self.index.docs_meta.get(doc_id, {})
        return 1.6 if meta.get("superseded_by") else 0.8

    def _store_concepts(self, query: str) -> list[str]:
        import re

        found = []
        for code in re.findall(r"\bs\d{2}\b", query.lower()):
            canonical = self.index.aliases.by_store_code(code)
            if canonical:
                found.append(canonical)
        return found

    def _hit(self, position: int, score: float, padded: bool = False) -> Hit:
        """构造一条命中。**doc_id 只从 chunk 自己取**——修 D12。

        starter 在这里之后又加了一行 `hit.doc_id = ordered[len(hits)].doc_id`，
        把正确的值覆写成了排序位置上那篇文档的编号。
        """
        chunk = self.index.chunks[position]
        text = chunk.text
        _safe, dropped = sanitize(text)
        return Hit(
            doc_id=chunk.doc_id,
            chunk_id=chunk.chunk_id,
            score=score,
            text=text,
            source_text=chunk.source_text,
            meta=self.index.docs_meta.get(chunk.doc_id, {}),
            kind=chunk.kind,
            table_header=chunk.table_header,
            dropped_instructions=dropped,
            padded=padded,
        )

    def search(
        self,
        query: str,
        top_k: int = 5,
        as_of: Optional[date] = None,
        store_id: Optional[str] = None,
        year: Optional[int] = None,
        window: Optional[tuple[str, str]] = None,
        numeric: bool = False,
        historical: Optional[bool] = None,
    ) -> SearchResult:
        as_of = as_of or self.today
        if historical is None:
            historical = _wants_historical(query)

        # ① 先过滤（D13）：allowed 只含合格文档的 chunk
        allowed, filtered = self._allowed(as_of, store_id, historical)
        if store_id:
            store_id = store_id.strip().upper()

        # ② 只在合格集合里打分
        scores = self.index.score_terms(self._weights(query), allowed)
        concepts, expansions = self._concept_scores(query, allowed)
        for position, score in concepts.items():
            if position in allowed:
                scores[position] = scores.get(position, 0.0) + score

        best_of_doc: dict[str, float] = {}
        for position, score in scores.items():
            doc_id = self.index.chunks[position].doc_id
            best_of_doc[doc_id] = max(best_of_doc.get(doc_id, 0.0), score)

        adjusted: list[tuple[float, int]] = []
        for position, score in scores.items():
            doc_id = self.index.chunks[position].doc_id
            total = score + DOC_PRIOR * best_of_doc.get(doc_id, 0.0)
            adjusted.append((
                total
                * self._multiplier(doc_id, as_of, store_id, year, window, numeric)
                * self._history_factor(doc_id, historical),
                position,
            ))
        adjusted.sort(key=lambda item: (-item[0], item[1]))

        # ③ 截取 top_k（此时已无需要事后剔除的东西）
        hits: list[Hit] = []
        taken: set[int] = set()
        per_doc: dict[str, int] = {}
        for score, position in adjusted:
            chunk = self.index.chunks[position]
            if per_doc.get(chunk.doc_id, 0) >= MAX_CHUNKS_PER_DOC:
                continue
            per_doc[chunk.doc_id] = per_doc.get(chunk.doc_id, 0) + 1
            taken.add(position)
            hits.append(self._hit(position, score))       # doc_id 不再被覆写
            if len(hits) >= top_k:
                break

        # 契约 §4：片段够的时候必须恰好给 top_k 条。每篇文档只占一格的规则
        # 可能让结果不足，这里在**合格集合内**按分数补齐；补上的标 padded。
        if len(hits) < top_k:
            remaining = [(score, position) for score, position in adjusted
                         if position not in taken]
            if len(remaining) < top_k - len(hits):
                rest = sorted(allowed - taken - {p for _s, p in adjusted})
                remaining.extend((0.0, position) for position in rest)
            for score, position in remaining:
                if len(hits) >= top_k:
                    break
                taken.add(position)
                hits.append(self._hit(position, score, padded=True))
            hits.sort(key=lambda hit: -hit.score)

        return SearchResult(
            hits=hits,
            query=query,
            terms=content_tokens(query),
            expansions=expansions,
            filtered=filtered,
            coverage=self._coverage(query, adjusted, top_k),
        )

    def _coverage(self, query: str, adjusted: list[tuple[float, int]], top_k: int) -> float:
        """问题被最好的那几个片段覆盖了多少。

        只看真正命中的片段：一个词都没命中时（"zzzqqq"），覆盖率就是 0，
        这是定义，不是异常——为了凑满 top_k 补上的片段不参与这个判断。
        """
        candidates = adjusted[: max(1, top_k)]
        if not candidates:
            return 0.0
        terms = content_tokens(query)
        return max(self.index.coverage(terms, position) for _, position in candidates)


def _wants_historical(query: str) -> bool:
    """问句里有没有"旧口径/以前/当时"这类信号。

    单独包一层是为了不把 `entities` 整个拖进来——那是个大模块，
    而 `/api/retrieve` 只需要认出这几个词。
    """
    try:
        from ..entities import wants_historical
    except Exception:                                     # pragma: no cover - 兜底
        lowered = (query or "").lower()
        return any(word in lowered for word in ("旧", "以前", "当时", "原来", "过去"))
    try:
        return bool(wants_historical(query))
    except Exception:                                     # pragma: no cover
        return False


def build_retriever(kb_dir: Path, index_path: Path, today: date,
                    rebuild: bool = False) -> Retriever:
    return Retriever(load_index(kb_dir, index_path, rebuild=rebuild), today)
