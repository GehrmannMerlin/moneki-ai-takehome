"""BM25 索引 + 内容感知的缓存键（修 D11）。

BM25 的数学部分（postings / idf / score_terms / coverage）沿用 starter，
那部分是对的；改的是缓存键与知识库内容的绑定，以及索引产物的落盘位置。

**修 D11**：starter 的 `content_key()` 只哈希三个版本常量、**完全不含知识库内容**：

```python
digest.update(("%s|%s|%s\\n" % (INDEX_VERSION, CHUNKER_VERSION, TOKENIZER_VERSION)).encode())
```

`kb_dir` 只出现在签名里。连带后果：`.cache/index.json` 被提交进仓库，
评委换 `knowledge_base/` 后缓存键不变、命中旧缓存，服务拿**上一套知识库**答题。
现在的键 = 版本常量 + 每个文件的(相对路径, 大小, mtime_ns, 内容 sha1)。
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .chunker import CHUNKER_VERSION, Chunk, chunk_documents
from .loader import Document, load_knowledge_base
from .tokenizer import TOKENIZER_VERSION, tokenize

INDEX_VERSION = "bm25-4"
K1 = 1.5
B = 0.75


def content_key(kb_dir: Path) -> str:
    """缓存键：版本常量 + 知识库**每个文件**的相对路径/大小/mtime/内容哈希。

    内容哈希是关键那一项：只看 mtime 的话，`git checkout` 回一份旧文件
    （mtime 变了但内容可能相同）会白重建；反过来，某些同步工具会保留 mtime，
    只改内容——那就漏重建了。三项一起算，两边都不吃亏。
    """
    digest = hashlib.sha256()
    digest.update(("%s|%s|%s\n" % (INDEX_VERSION, CHUNKER_VERSION, TOKENIZER_VERSION)).encode())
    root = Path(kb_dir)
    if not root.exists():
        digest.update(b"<missing>")
        return digest.hexdigest()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.name.startswith("."):
            continue
        stat = path.stat()
        digest.update(("%s|%d|%d|" % (
            path.relative_to(root).as_posix(), stat.st_size, stat.st_mtime_ns)).encode())
        digest.update(hashlib.sha1(path.read_bytes()).hexdigest().encode())
        digest.update(b"\n")
    return digest.hexdigest()


@dataclass
class Posting:
    chunk_index: int
    freq: int


class BM25Index:
    """只依赖标准库的 BM25，几十篇文档够快了。"""

    def __init__(
        self,
        chunks: list[Chunk],
        docs_meta: dict[str, dict],
        aliases,
        key: str,
        warnings: Optional[list[str]] = None,
        texts: Optional[dict[str, str]] = None,
    ) -> None:
        self.chunks = chunks
        self.docs_meta = docs_meta
        #: doc_id -> 文档可见正文全文。引用要逐字核对，必须留着原文。
        self.texts = texts or {}
        self.aliases = aliases
        self.key = key
        self.warnings = warnings or []
        self.doc_freq: dict[str, int] = {}
        self.postings: dict[str, list[Posting]] = {}
        self.lengths: list[int] = []
        self.avg_length = 1.0
        self._build()

    def _tokens_of(self, chunk: Chunk) -> list[str]:
        """入库前做一次别名归一：英文邮件里的 Salmon 也带上"三文鱼poke"的词。

        两边都归一到数据库的写法，中文问句才有机会命中英文文档。
        """
        tokens = tokenize(chunk.text)
        lowered = chunk.text.lower()
        for canonical in self.aliases.strict_mentions(chunk.text):
            if canonical.lower() not in lowered:
                tokens.extend(tokenize(canonical))
        return tokens

    def _build(self) -> None:
        for position, chunk in enumerate(self.chunks):
            counts = Counter(self._tokens_of(chunk))
            self.lengths.append(sum(counts.values()) or 1)
            for term, freq in counts.items():
                self.postings.setdefault(term, []).append(Posting(position, freq))
                self.doc_freq[term] = self.doc_freq.get(term, 0) + 1
        self.avg_length = (sum(self.lengths) / len(self.lengths)) if self.lengths else 1.0

    # -- 检索 -------------------------------------------------------------------

    def idf(self, term: str) -> float:
        n = len(self.chunks)
        df = self.doc_freq.get(term, 0)
        if df == 0:
            return 0.0
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def score_terms(
        self, weights: dict[str, float], allowed: Optional[set[int]] = None
    ) -> dict[int, float]:
        """返回 {块下标: 分数}。`allowed` 是元数据过滤之后还留在场上的块。"""
        scores: dict[int, float] = {}
        for term, weight in weights.items():
            idf = self.idf(term)
            if idf <= 0:
                continue
            for posting in self.postings.get(term, ()):
                if allowed is not None and posting.chunk_index not in allowed:
                    continue
                length = self.lengths[posting.chunk_index]
                tf = posting.freq
                part = idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / self.avg_length))
                scores[posting.chunk_index] = scores.get(posting.chunk_index, 0.0) + part * weight
        return scores

    @property
    def max_idf(self) -> float:
        return math.log(1 + (len(self.chunks) + 0.5) / 0.5)

    def _weight_of(self, term: str) -> float:
        """整个语料里都没有的词，权重按最大 IDF 算。

        这正是"知识库里根本没提过这件事"的信号：工资、天气这类问题的词
        在语料里一个都找不到，覆盖率会掉到很低，据此拒答而不是硬答。
        """
        return self.idf(term) if self.doc_freq.get(term) else self.max_idf

    def coverage(self, terms: list[str], chunk_index: int) -> float:
        """查询里有多少（按 IDF 加权的）词真的出现在这个块里。"""
        if not terms:
            return 0.0
        chunk_terms = set(self._tokens_of(self.chunks[chunk_index]))
        unique = set(terms)
        total = sum(self._weight_of(term) for term in unique) or 1.0
        hit = sum(self._weight_of(term) for term in unique if term in chunk_terms)
        return hit / total

    def chunks_of(self, doc_id: str) -> list[Chunk]:
        return [chunk for chunk in self.chunks if chunk.doc_id == doc_id]

    # -- 持久化 -----------------------------------------------------------------

    def to_json(self) -> dict:
        return {
            "key": self.key,
            "version": INDEX_VERSION,
            "docs": self.docs_meta,
            "aliases": self.aliases.to_json(),
            "warnings": self.warnings,
            "texts": self.texts,
            "chunks": [chunk.as_dict() for chunk in self.chunks],
        }

    @classmethod
    def from_json(cls, payload: dict) -> "BM25Index":
        from .aliases import AliasTable

        chunks = [Chunk(**item) for item in payload["chunks"]]
        return cls(
            chunks=chunks,
            docs_meta=payload["docs"],
            aliases=AliasTable.from_json(payload.get("aliases") or {}),
            key=payload.get("key", ""),
            warnings=payload.get("warnings") or [],
            texts=payload.get("texts") or {},
        )


def build_index(kb_dir: Path, prepare_tokenizer: bool = True) -> BM25Index:
    """从知识库目录完整重建索引。"""
    from .aliases import build_alias_table

    documents, warnings = load_knowledge_base(kb_dir)
    aliases = build_alias_table(documents)
    if prepare_tokenizer:
        _prepare_tokenizer(aliases)
    chunks = chunk_documents(documents)
    docs_meta = {document.doc_id: document.meta() for document in documents}
    texts = {document.doc_id: document.text for document in documents}
    return BM25Index(chunks, docs_meta, aliases, content_key(kb_dir), warnings, texts)


def _prepare_tokenizer(aliases) -> None:
    """把别名词典的全部写法挂进 jieba 自定义词典。

    不做这一步，`牛肉poke` 会被切成 `牛肉`+`poke`、`Makai Poke` 会被切成
    `makai`+`poke`，别名表的整词判定就接不上了。
    """
    from .tokenizer import init_jieba

    words: list[str] = []
    for canonical, names in (aliases.aliases_of or {}).items():
        words.append(canonical)
        words.extend(names)
    words.extend(aliases.canonical_of.keys())
    for alias in list(aliases.canonical_of.keys()):
        words.append(alias)
    init_jieba(words)


def save_index(index: BM25Index, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(index.to_json(), handle, ensure_ascii=False)


def load_index(kb_dir: Path, path: Path, rebuild: bool = False) -> BM25Index:
    """缓存命中就直接用，内容对不上就重建。

    判据是 `content_key`（含知识库内容哈希），所以换了知识库一定重建。
    """
    key = content_key(kb_dir)
    if not rebuild and Path(path).exists():
        try:
            with Path(path).open(encoding="utf-8") as handle:
                payload = json.load(handle)
            if payload.get("key") == key and payload.get("version") == INDEX_VERSION:
                index = BM25Index.from_json(payload)
                _prepare_tokenizer(index.aliases)
                return index
        except (ValueError, KeyError, TypeError):
            pass
    index = build_index(kb_dir)
    save_index(index, path)
    return index


def documents_of(index: BM25Index) -> list[Document]:    # pragma: no cover - 调试辅助
    return list(index.docs_meta.values())
