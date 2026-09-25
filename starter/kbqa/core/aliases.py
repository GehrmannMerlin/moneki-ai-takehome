"""别名表：商品和门店的各种叫法从知识库的别名词典里读，代码里不写死。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .loader import Document
from .tokenizer import normalise

_SPLIT = re.compile(r"[、,，/;；]|\s{2,}")
_STORE_CODE = re.compile(r"^S\d{2}$", re.I)
_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|[\s:|-]+\|\s*$")

#: 表头里出现这些词，就认为这张表是别名表。
_CANON_HINTS = ("数据库写法", "标准写法", "canonical")
_ALIAS_HINTS = ("别名", "叫法", "alias")


@dataclass
class AliasTable:
    canonical_of: dict[str, str] = field(default_factory=dict)
    """规范化后的别名 -> 数据库写法。"""
    aliases_of: dict[str, list[str]] = field(default_factory=dict)
    """数据库写法 -> 全部别名（含自己）。"""
    store_code_of: dict[str, str] = field(default_factory=dict)
    """数据库写法 -> 门店编号（别名表里给了编号列时才有）。"""
    source_doc: str = ""
    _distinctive: Optional[dict[str, str]] = field(default=None, repr=False, compare=False)

    def to_json(self) -> dict:
        return {
            "canonical_of": self.canonical_of,
            "aliases_of": self.aliases_of,
            "store_code_of": self.store_code_of,
            "source_doc": self.source_doc,
        }

    @classmethod
    def from_json(cls, payload: dict) -> "AliasTable":
        return cls(
            canonical_of=payload.get("canonical_of") or {},
            aliases_of=payload.get("aliases_of") or {},
            store_code_of=payload.get("store_code_of") or {},
            source_doc=payload.get("source_doc") or "",
        )

    def resolve(self, phrase: str) -> str:
        """把任意写法归一到数据库写法；不认识就原样返回。"""
        return self.canonical_of.get(normalise(phrase), phrase)

    def variants(self, canonical: str) -> list[str]:
        return self.aliases_of.get(canonical, [canonical])

    def mentions(self, text: str) -> list[str]:
        """文本里出现了哪些（别名或数据库写法所指的）对象，按出现位置排序。

        既认整词（"牛肉poke"），也认"说了一半"的写法：
        "三文鱼那次断供"里的"三文鱼"要能认出"三文鱼poke"这个对象，
        这样才可能检索到只写 Salmon 的英文邮件（R04）。
        """
        from .tokenizer import tokenize

        lowered = normalise(text)
        query_tokens = set(tokenize(text))
        found: list[tuple[int, str]] = []
        for alias, canonical in self.canonical_of.items():
            if len(alias) < 2:
                continue
            position = lowered.find(alias)
            if position < 0:
                if not self._partial_match(lowered, alias, query_tokens):
                    continue
                position = len(lowered)
            found.append((position, canonical))
        seen: set[str] = set()
        ordered: list[str] = []
        for _, canonical in sorted(found):
            if canonical not in seen:
                seen.add(canonical)
                ordered.append(canonical)
        return ordered

    @staticmethod
    def _partial_match(lowered: str, alias: str, query_tokens: set[str]) -> bool:
        """问句里"说了一半"的别名，算不算提到了这个对象。

        两条判据，命中任一条即可：

        1. **前缀子串，且不短于 3 字**：`三文鱼` ⊂ `三文鱼poke`。
           3 字是这条规则的全部安全边际——`牛肉饭` 与 `牛肉poke` 的共同前缀只有
           `牛肉` 两个字，所以不会被认成同一件商品（KB-003 明确警告过这两个别混）。
        2. **词元重合 ≥ 0.6**：`味噌拉面` 与 `味增拉面` 这类分词切不到一起的写法。
           0.6 而不是 0.5：`牛肉poke` 与 `Makai Poke` 正好共享一个 `poke`，
           各占一半，按 0.5 放行会把商品认成门店。

        **只认前缀，不认后缀**——这一条是踩出来的：一开始前缀后缀都认，
        结果 `照烧三明治` 的后缀 `三明治` 让"吞拿鱼三明治"和"照烧三明治"
        在问 S04 那题时互相污染（R10 从绿变红）。
        `三明治` 是**品类词**，出现在很多别名里，它不指向任何一个具体商品；
        而 `三文鱼` 是**产品词头**，指向性明确。前缀规则天然区分了这两者。

        子串判据必须严于"重合一个词"——否则"断供"这种横跨多篇文档的词
        会让一堆无关文档被认成同一件事。
        """
        from .tokenizer import tokenize

        for cut in range(3, len(alias)):
            if alias[:cut] in lowered:
                return True
        alias_tokens = set(tokenize(alias))
        if not alias_tokens:
            return False
        return len(alias_tokens & query_tokens) / len(alias_tokens) >= 0.6

    def distinctive_tokens(self) -> dict[str, str]:
        """只属于某一个对象的英文词 -> 该对象的数据库写法。

        英文邮件通常只写 `salmon`，不会写全 `Salmon Poke`，所以整词匹配接不上。
        这里取“在整张别名表里只指向一个对象”的英文词（长度不小于 4）做桥，
        `poke` 这种横跨三个商品的词自然被排除，不会误伤。
        """
        from .tokenizer import tokenize

        if self._distinctive is None:
            owners: dict[str, set[str]] = {}
            for canonical, names in self.aliases_of.items():
                for name in names:
                    for token in tokenize(name):
                        if len(token) >= 4 and token.isascii():
                            owners.setdefault(token, set()).add(canonical)
            self._distinctive = {
                token: next(iter(holders))
                for token, holders in owners.items()
                if len(holders) == 1
            }
        return self._distinctive

    def strict_mentions(self, text: str) -> list[str]:
        """文档侧的别名归一：只认整词命中，宁可漏，不能错。

        英文别名要求词边界且不短于 3 个字符——英文邮件里到处都是 `as`，
        用它去撞门店别名 `AS` 会把整封邮件污染掉。
        """
        lowered = normalise(text)
        found: list[str] = []
        for alias, canonical in self.canonical_of.items():
            if canonical in found:
                continue
            if re.fullmatch(r"[a-z0-9 .\-]+", alias):
                if len(alias.replace(" ", "")) < 3:
                    continue
                if not re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(alias), lowered):
                    continue
            elif alias not in lowered:
                continue
            found.append(canonical)
        for token, canonical in self.distinctive_tokens().items():
            if canonical in found:
                continue
            if re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(token), lowered):
                found.append(canonical)
        return found

    def by_store_code(self, code: str) -> str:
        """门店编号 -> 数据库写法。"""
        code = (code or "").strip().upper()
        for canonical, store_code in self.store_code_of.items():
            if store_code == code:
                return canonical
        return ""

    def expansions(self, text: str) -> list[str]:
        """查询扩写用：文本里提到的每个对象，把它的其它写法都补上。

        问句里直接写门店编号（`S05`）时，把门店名与它的别名也补进去。
        """
        extra: list[str] = []
        lowered = normalise(text)
        mentioned = list(self.mentions(text))
        for code in re.findall(r"\bs\d{2}\b", lowered):
            canonical = self.by_store_code(code)
            if canonical and canonical not in mentioned:
                mentioned.append(canonical)
        for canonical in mentioned:
            for variant in self.variants(canonical):
                if normalise(variant) not in lowered:
                    extra.append(variant)
        return extra


def _cells(line: str) -> list[str]:
    match = _TABLE_ROW.match(line)
    return [cell.strip() for cell in match.group(1).split("|")] if match else []


def _tables(text: str) -> Iterable[tuple[list[str], list[list[str]]]]:
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        if not _TABLE_ROW.match(lines[index]):
            index += 1
            continue
        header = _cells(lines[index])
        if index + 1 >= len(lines) or not _TABLE_SEP.match(lines[index + 1]):
            index += 1
            continue
        rows = []
        cursor = index + 2
        while cursor < len(lines) and _TABLE_ROW.match(lines[cursor]):
            rows.append(_cells(lines[cursor]))
            cursor += 1
        yield header, rows
        index = cursor


def _split_aliases(cell: str) -> list[str]:
    return [part.strip() for part in _SPLIT.split(cell) if part.strip()]


def build_alias_table(documents: list[Document]) -> AliasTable:
    """扫描全部文档，找出别名表并解析。找不到就返回空表，系统照常工作。"""
    table = AliasTable()
    for document in documents:
        for header, rows in _tables(document.text):
            lowered = [cell.lower() for cell in header]
            if not any(hint in cell for cell in lowered for hint in _CANON_HINTS):
                continue
            alias_columns = [
                position
                for position, cell in enumerate(lowered)
                if any(hint in cell for hint in _ALIAS_HINTS)
            ]
            if not alias_columns:
                continue
            table.source_doc = document.doc_id
            for row in rows:
                if not row or not row[0]:
                    continue
                canonical = row[0].strip()
                names = [canonical]
                for column in alias_columns:
                    if column < len(row):
                        names.extend(_split_aliases(row[column]))
                for cell in row[1:]:
                    if _STORE_CODE.match(cell.strip()):
                        table.store_code_of[canonical] = cell.strip().upper()
                unique: list[str] = []
                for name in names:
                    if name and name not in unique:
                        unique.append(name)
                table.aliases_of[canonical] = unique
                for name in unique:
                    table.canonical_of.setdefault(normalise(name), canonical)
    return table
