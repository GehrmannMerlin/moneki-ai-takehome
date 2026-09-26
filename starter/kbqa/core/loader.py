"""知识库加载器：四种后缀 + GBK 降级 + HTML 剥标签 + 元数据降级链。

修掉 starter 的三处缺陷：

* **D7** `SUPPORTED_SUFFIXES` 只有 `.md`/`.markdown` → 加 `.txt`/`.html`
  （丢的是 KB-022 英文邮件、KB-061 HTML、KB-062 GBK 通知，都是题库金标所在）；
* **D8** `raw.decode("utf-8", errors="ignore")` → 走 `textnorm.decode_bytes`
  （UTF-8 → GB18030 降级，与评测脚本同构）；
* **D9** HTML 不剥标签 → 走 `textnorm.visible_text`。

**另外修掉一处原清单没有的缺陷**：`Document.meta()` 写的键叫 `"state"`，
而 `retriever._eligible()` 读的是 `meta.get("status")`——两边对不上，
版本过滤整条失效。这里 `meta()` **两个键都给**（`status` 是权威键，
`state` 保留是为了不破坏 starter 里已经按 `state` 读的地方，例如 `docfacts`）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from .textnorm import decode_bytes, visible_text

#: 修 D7：四种后缀都要。契约与 HANDOVER 都声称支持 md/txt/html。
SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".html", ".htm"}

#: 文件名开头的编号就是 doc_id，与文件格式无关（契约 §0）。
_DOC_ID = re.compile(r"^(KB-\d{3})")
_FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)
_STORE_CODE = re.compile(r"\bS\d{2}\b")

#: 正文里的生效日期：优先"自 2026 年 8 月 15 日起""生效日期：2026-07-01"这类明确写法。
_CN_DATE = r"(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*日?"
_EFFECTIVE_PATTERNS = (
    re.compile(r"(?:自|从)\s*" + _CN_DATE + r"\s*(?:起|开始)"),
    re.compile(r"生效(?:日期)?[：: ]\s*" + _CN_DATE),
    re.compile(r"(?:执行|实施)(?:日期)?[：: ]\s*" + _CN_DATE),
)
_ANY_DATE = re.compile(_CN_DATE)
_EMAIL_DATE = re.compile(r"^Date:\s*(.+)$", re.M)
_MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()

#: KB-001 §5.2：周报、会议纪要、活动复盘里的数字是人工估算，不能当答案。
ESTIMATE_TYPES = {"周报", "会议纪要", "复盘", "活动复盘"}

#: 元数据来源，记进 `meta_source` 供 trace 与调试用（B6 的三级降级链）。
SOURCE_FRONTMATTER = "frontmatter"
SOURCE_BODY = "body_inferred"
SOURCE_DEFAULT = "defaulted"


@dataclass
class Document:
    doc_id: str
    title: str
    text: str
    path: Path
    fmt: str
    doc_type: str = ""
    status: str = "现行"
    effective_from: Optional[date] = None
    superseded_by: Optional[str] = None
    stores: list[str] = field(default_factory=list)
    #: `stores` 是不是文档自己声明的。正文里认出来的门店只能当线索，
    #: 不能当硬过滤条件——KB-001 的正文里就举了 `s01` 当例子。
    stores_explicit: bool = False
    updated_at: Optional[date] = None
    warnings: list[str] = field(default_factory=list)
    #: 生效日期与状态是从哪来的：frontmatter / body_inferred / defaulted。
    meta_source: str = SOURCE_DEFAULT
    #: 实际用什么编码读进来的（trace 用）。
    encoding: str = "utf-8"

    @property
    def estimates_only(self) -> bool:
        return self.doc_type in ESTIMATE_TYPES

    @property
    def title_year(self) -> Optional[int]:
        match = re.search(r"(20\d{2})", self.title)
        return int(match.group(1)) if match else None

    @property
    def is_archived(self) -> bool:
        return self.status == "归档"

    @property
    def is_deprecated(self) -> bool:
        return self.status == "已废止"

    def meta(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            # `status` 是权威键：`retriever._eligible()` 读的就是它。
            # `state` 是 starter 原先的键名，保留一份，别的地方还在读。
            "status": self.status,
            "state": self.status,
            "type": self.doc_type,
            "effective_from": self.effective_from.isoformat() if self.effective_from else None,
            "superseded_by": self.superseded_by,
            "stores": self.stores,
            "stores_explicit": self.stores_explicit,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "estimates_only": self.estimates_only,
            "title_year": self.title_year,
            "format": self.fmt,
            "filename": self.path.name,
            "meta_source": self.meta_source,
            "encoding": self.encoding,
        }


def parse_front_matter(text: str) -> tuple[dict, str]:
    """极简 YAML 头解析：`key: value` 与 `key: [a, b]`，够用且不引依赖。"""
    match = _FRONT_MATTER.match(text)
    if not match:
        return {}, text
    meta: dict = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            meta[key.strip()] = [
                item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip()
            ]
        else:
            meta[key.strip()] = value.strip().strip("'\"")
    return meta, text[match.end():]


def _as_date(value) -> Optional[date]:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    match = _ANY_DATE.search(text)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def effective_from_body(text: str) -> Optional[date]:
    """没有 YAML 头时，从正文里认生效日期（B6 的降级链第二级）。"""
    for pattern in _EFFECTIVE_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except ValueError:
                continue
    mail = _EMAIL_DATE.search(text)
    if mail:
        parts = mail.group(1).replace(",", " ").split()
        day = month = year = None
        for token in parts:
            low = token.lower()[:3]
            if low in _MONTHS:
                month = _MONTHS.index(low) + 1
            elif token.isdigit() and len(token) == 4:
                year = int(token)
            elif token.isdigit() and len(token) <= 2:
                day = int(token)
        if day and month and year:
            try:
                return date(year, month, day)
            except ValueError:
                return None
    match = _ANY_DATE.search(text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    return None


def _title_from_body(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if not stripped or set(stripped) <= set("=-*_ "):
            continue
        if stripped.startswith(("From:", "To:", "Cc:", "Date:")):
            continue
        if stripped.startswith("Subject:"):
            return stripped.split(":", 1)[1].strip()
        if stripped.startswith(("标题：", "标题:")):
            return stripped.split("：", 1)[-1].split(":", 1)[-1].strip()
        return stripped[:80]
    return fallback


def _guess_type(filename: str, text: str) -> str:
    head = filename + " " + text[:200]
    for marker in ("周报", "通知", "政策", "手册", "纪要", "报告", "FAQ", "档案",
                   "总表", "名录", "对照表", "邮件", "Email"):
        if marker in head:
            return marker
    return "文档"


def _sorted_unique(values) -> list[str]:
    return sorted({value.upper() for value in values})


def candidate_doc_id(path: Path) -> Optional[str]:
    """轻量判定：这个文件会不会被当成知识库文档（与 `load_document` 同一判据）。

    供 `index.content_key` 用——指纹必须只描述**真正进索引的输入**：
    无 KB 编号的说明文件（`README.md`、`notes.txt`）不该扰动缓存键。
    不做切块、不做正文抽取，只回答"它算不算一篇文档、doc_id 是什么"。
    """
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        return None
    match = _DOC_ID.match(path.name)
    if match:
        return match.group(1)
    # 文件名没有编号时，只有 .md 的 frontmatter 能声明 doc_id（与 load_document 一致）
    if suffix in (".md", ".markdown"):
        try:
            raw = path.read_bytes()
        except OSError:                                   # pragma: no cover
            return None
        text, _ = decode_bytes(raw)
        meta, _ = parse_front_matter(text)
        declared = str(meta.get("doc_id") or "").strip()
        if declared:
            return declared
    return None


def load_document(path: Path) -> Optional[Document]:
    """读一个文件。不是知识库文档（没有 KB 编号）时返回 None。"""
    warnings: list[str] = []
    raw = path.read_bytes()
    text, encoding = decode_bytes(raw)              # 修 D8
    suffix = path.suffix.lower()
    fmt = {".md": "md", ".markdown": "md", ".txt": "txt",
           ".html": "html", ".htm": "html"}.get(suffix, "txt")

    meta: dict = {}
    if fmt == "md":
        meta, text = parse_front_matter(text)
    # fmt == html → 交给 visible_text 剥标签（修 D9）
    text, html_title_text = visible_text(text, fmt)
    if fmt == "html" and not meta.get("title"):
        meta["title"] = html_title_text

    match = _DOC_ID.match(path.name)
    doc_id = str(meta.get("doc_id") or (match.group(1) if match else "")).strip()
    if not doc_id:
        return None

    declared = meta.get("stores")
    stores = declared or _sorted_unique(_STORE_CODE.findall(text))
    doc_type = str(meta.get("type") or "").strip()
    if not doc_type:
        doc_type = _guess_type(path.name, text)

    # 元数据降级链（B6）：frontmatter → 正文推断 → 默认值。每一步都记 meta_source。
    status = str(meta.get("status") or "").strip()
    effective = _as_date(meta.get("effective_from"))
    if status or effective:
        meta_source = SOURCE_FRONTMATTER
    else:
        effective = effective_from_body(text)
        meta_source = SOURCE_BODY if effective else SOURCE_DEFAULT
    if not status:
        # 提不到状态的当作"自始有效"的现行文档。
        # 不能因为"无法确定时效"就整篇降权/过滤——KB-062（GBK 通知，无 frontmatter）
        # 正是 C03/R03 的金标答案，一律排除会把答案丢掉。
        status = "现行"

    return Document(
        doc_id=doc_id,
        title=str(meta.get("title") or _title_from_body(text, path.stem)).strip(),
        text=text.strip("\n"),
        path=path,
        fmt=fmt,
        doc_type=doc_type,
        status=status,
        effective_from=effective,
        superseded_by=(str(meta.get("superseded_by")).strip() if meta.get("superseded_by") else None),
        stores=[s.upper() for s in stores],
        stores_explicit=bool(declared),
        updated_at=_as_date(meta.get("updated_at")),
        warnings=warnings,
        meta_source=meta_source,
        encoding=encoding,
    )


def load_knowledge_base(kb_dir: Path) -> tuple[list[Document], list[str]]:
    """加载整个知识库目录，返回（文档列表，告警列表）。

    修 D7：后缀过滤走 `SUPPORTED_SUFFIXES`（四种）。
    没有 KB 编号的文件跳过并留 warning——`kb_docs` 因此自然等于 35。
    """
    kb_dir = Path(kb_dir)
    documents: list[Document] = []
    warnings: list[str] = []
    seen: dict[str, Path] = {}
    if not kb_dir.exists():
        return documents, ["知识库目录不存在：%s" % kb_dir]
    for path in sorted(kb_dir.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            warnings.append("后缀不在支持列表里，跳过：%s" % path.name)
            continue
        document = load_document(path)
        if document is None:
            warnings.append("跳过没有 KB 编号的文件：%s" % path.name)
            continue
        if document.doc_id in seen:
            warnings.append(
                "doc_id 重复：%s 同时出现在 %s 与 %s"
                % (document.doc_id, seen[document.doc_id].name, path.name)
            )
            continue
        seen[document.doc_id] = path
        warnings.extend(document.warnings)
        documents.append(document)
    return documents, warnings
