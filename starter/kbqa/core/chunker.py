"""标题层级感知切块：不丢文尾，不把切点落在句子中间。

修 starter 的 D10（`chunker.py:41`）：

```python
for number, start in enumerate(range(0, len(text) - CHUNK_SIZE, CHUNK_SIZE), start=1):
```

`range(0, len(text) - 300, 300)` 的上界算错：`len(text)=1000` 时得到 0/300/600，
`text[600:900]` 之后那 100 字永远不会被任何 chunk 覆盖。短于 300 字的文档
更是落进 `if not chunks` 分支，整篇变一块。而且定长切点落在句子中间，
"退款时限"的标题可能和上一节最后半句话粘在一起。

本实现的**覆盖不变式**：把一篇文档的 chunks 按顺序拼起来，逐字等于
`document.text`。测试 `test_full_coverage` 断言这一点——
切块可以决定"怎么切"，但不许决定"丢什么"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .loader import Document

#: 切块参数变了，索引缓存必须失效，所以写进缓存键里。
CHUNKER_VERSION = "chunker-3"

#: 太短的段与相邻段合并；太长的段按段落/行/句子再切。
#: `MIN_CHARS` 是**合并阈值**，不是硬下限——单独一句话的段落（"## 五、复核"）
#: 只有几十字，硬撑到 120 只能靠把不相关内容粘起来，反而伤检索。
MIN_CHARS = 120
#: 目标上限。**不是硬上限**：按句切时允许超出"一个句子"的长度，
#: 所以实际最大块会到 500~600。要求"任何块都不超过 700"是能保证的，
#: 要求"都 ≤500"不能——除非把句子从中间劈开，那正是要避免的事。
TARGET_MAX = 500
#: 硬上限：任何 chunk 都不许超过它。超长段落（Markdown 表格）必须走二次切分，
#: 否则一整张 1297 字的表会把整篇的检索信号糊成一团。
HARD_MAX = 700

#: Markdown 标题：`#` 到 `######`。
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
#: 全角/中文编号标题行：`一、xxx`、`（二）xxx`、`3. xxx`
_CN_HEADING = re.compile(r"^(?:[一二三四五六七八九十]+[、.．]|（[一二三四五六七八九十]+）|\d+[、.．])\s*\S")
#: 分隔线：`---`、`===` 这类不是内容，但也不算标题。
_RULE = re.compile(r"^\s*[-=*_]{3,}\s*$")


@dataclass
class Chunk:
    doc_id: str
    chunk_id: str
    text: str
    source_text: str
    heading: str = ""
    kind: str = "text"
    table_header: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "chunk_id": self.chunk_id,
            "text": self.text,
            "source_text": self.source_text,
            "heading": self.heading,
            "kind": self.kind,
            "table_header": self.table_header,
        }


@dataclass
class _Segment:
    """一个候选片段：正文 + 它所属的标题路径 + 在原文里的起止偏移。"""

    start: int
    end: int
    heading: str


def _heading_of(line: str) -> str:
    md = _MD_HEADING.match(line)
    if md:
        return md.group(2).strip()
    stripped = line.strip()
    if _CN_HEADING.match(stripped) and len(stripped) <= 40:
        return stripped
    return ""


def _segments(text: str) -> list[_Segment]:
    """按标题行把正文切成段，每段记录 heading 路径与偏移。

    偏移是相对 `text` 的，所以后面**直接切片**——不重新拼接字符串，
    这是"拼接 == 原文"能成立的关键。
    """
    if not text:
        return []
    lines = text.split("\n")
    segments: list[_Segment] = []
    heading_stack: list[tuple[int, str]] = []          # (层级, 标题)
    current_heading = ""
    offset = 0
    seg_start = 0
    seg_heading = ""

    def flush(end: int) -> None:
        if end > seg_start and text[seg_start:end].strip():
            segments.append(_Segment(seg_start, end, seg_heading))

    for line in lines:
        line_end = offset + len(line)
        heading = _heading_of(line)
        if heading and not _RULE.match(line):
            flush(offset)
            level = len(_MD_HEADING.match(line).group(1)) if _MD_HEADING.match(line) else 1
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, heading))
            current_heading = " > ".join(item[1] for item in heading_stack)
            seg_start = offset
            seg_heading = current_heading
        offset = line_end + 1                            # +1 = 换行符
    flush(len(text))
    return segments


def _split_long(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """一段超过 TARGET_MAX 时按段落切；段落还是太长就按行切、再按句子切。

    返回的偏移区间**首尾相接且完整覆盖** `[start, end)`。
    """
    piece = text[start:end]
    if len(piece) <= TARGET_MAX:
        return [(start, end)]

    # ① 先按空行（段落边界）切
    bounds: list[tuple[int, int]] = []
    cursor = start
    for match in re.finditer(r"\n\s*\n", piece):
        cut = start + match.end()
        if cut - cursor > 0:
            bounds.append((cursor, cut))
        cursor = cut
    if cursor < end:
        bounds.append((cursor, end))
    if not bounds:
        bounds = [(start, end)]

    # ② 段落还超长：先按行切（Markdown 表格是一整"段"，只有按行才能切开）
    line_split: list[tuple[int, int]] = []
    for lo, hi in bounds:
        if hi - lo <= TARGET_MAX:
            line_split.append((lo, hi))
            continue
        line_split.extend(_split_by_line(text, lo, hi))

    # ③ 按行切完还超长：按句子切
    out: list[tuple[int, int]] = []
    for lo, hi in line_split:
        if hi - lo <= TARGET_MAX:
            out.append((lo, hi))
            continue
        out.extend(_split_by_sentence(text, lo, hi))
    return out


def _split_by_line(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """按行累积到 TARGET_MAX 就切一刀。

    表格必须走这条路：`| a | b |` 行之间没有空行，段落切法对它无效，
    一块 1297 字的过敏原表会把整篇的检索信号糊成一团。
    """
    piece = text[start:end]
    out: list[tuple[int, int]] = []
    cursor = start
    for match in re.finditer(r"\n", piece):
        cut = start + match.end()
        if cut - cursor >= TARGET_MAX:
            out.append((cursor, cut))
            cursor = cut
    if cursor < end:
        out.append((cursor, end))
    return out


_SENTENCE_END = re.compile(r"[。！？!?；;]\s*")


def _split_by_sentence(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """按句末标点切，尽量让每块接近 TARGET_MAX。同样保持首尾相接。"""
    piece = text[start:end]
    cuts = [start + match.end() for match in _SENTENCE_END.finditer(piece)]
    if not cuts or cuts[-1] != end:
        cuts.append(end)
    out: list[tuple[int, int]] = []
    cursor = start
    for cut in cuts:
        if cut <= cursor:
            continue
        if cut - cursor >= TARGET_MAX or cut == end:
            out.append((cursor, cut))
            cursor = cut
    if cursor < end:
        if out:
            out[-1] = (out[-1][0], end)                  # 尾巴并进最后一块
        else:
            out.append((cursor, end))
    return out


def _merge_small(pieces: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """把相邻的小片段打包到接近 TARGET_MAX，但不在标题边界上乱粘。

    规则（按优先级）：

    1. **同标题**的相邻片段一直并到再并就超 TARGET_MAX 为止；
    2. **不同标题**时，只有当"下一块自己太短（< MIN_CHARS）"才并进来——
       这样"## 五、复核"这种只剩一句话的小节会跟上一节合并，
       而两个内容量都够的章节不会被搅在一起。

    合并只改"哪些偏移合成一块"，不改偏移本身，所以覆盖不变式不受影响。
    """
    packed: list[tuple[int, int, str]] = []
    for start, end, heading in pieces:
        if packed:
            prev_start, prev_end, prev_heading = packed[-1]
            grown = end - prev_start
            same_heading = prev_heading == heading
            too_short_to_stand_alone = (end - start) < MIN_CHARS
            if grown <= TARGET_MAX and (same_heading or too_short_to_stand_alone):
                packed[-1] = (prev_start, end, prev_heading)
                continue
        packed.append((start, end, heading))
    return packed


def chunk_document(document: Document) -> list[Chunk]:
    """把一篇文档切成 chunks。

    `chunk.text` 是**原文切片**（不 strip、不重排），所以
    `"".join(chunk.text for chunk in chunks) == document.text`。
    """
    text = document.text
    if not text:
        return [Chunk(document.doc_id, "%s#1" % document.doc_id,
                      document.title, document.title, heading=document.title)]

    pieces: list[tuple[int, int, str]] = []
    for segment in _segments(text):
        for lo, hi in _split_long(text, segment.start, segment.end):
            pieces.append((lo, hi, segment.heading))

    merged = _merge_small(pieces)
    if not merged:
        merged = [(0, len(text), "")]

    # 收尾：把最后一块的尾巴补齐到文末，保证一个字都不丢
    if merged[-1][1] != len(text):
        start, _end, heading = merged[-1]
        merged[-1] = (start, len(text), heading)
    # 补掉任何可能的空隙（相邻区间必须首尾相接）
    fixed: list[tuple[int, int, str]] = []
    cursor = 0
    for start, end, heading in merged:
        if start > cursor:
            fixed.append((cursor, start, heading))
        fixed.append((start, end, heading))
        cursor = end
    if cursor < len(text):
        fixed.append((cursor, len(text), fixed[-1][2] if fixed else ""))

    chunks: list[Chunk] = []
    for number, (start, end, heading) in enumerate(fixed, start=1):
        piece = text[start:end]
        if not piece:
            continue
        chunks.append(Chunk(
            doc_id=document.doc_id,
            chunk_id="%s#%d" % (document.doc_id, number),
            text=piece,
            source_text=piece,
            heading=heading or document.title,
            kind="table" if _looks_like_table(piece) else "text",
            table_header=_table_header(piece),
        ))
    if not chunks:
        chunks = [Chunk(document.doc_id, "%s#1" % document.doc_id,
                        text, text, heading=document.title)]
    return chunks


def _looks_like_table(piece: str) -> bool:
    rows = [line for line in piece.splitlines() if line.strip().startswith("|")]
    return len(rows) >= 2


def _table_header(piece: str) -> list[str]:
    for line in piece.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and set(stripped) - set("|-: \t"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if any(cells):
                return cells
    return []


def chunk_documents(documents: list[Document]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for document in documents:
        chunks.extend(chunk_document(document))
    return chunks
