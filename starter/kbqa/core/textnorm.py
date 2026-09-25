"""可见正文管线：与评测脚本 `eval/run_eval.py` **同构**。

这是 quote 逐字校验（`quotes_verbatim`，每轮必查）不红的唯一稳妥办法：
文档侧怎么规范化，必须和评测脚本一模一样。三个函数是从
`eval/run_eval.py` 逐字搬过来的，注释里标了出处行号——
**改这里之前先去看评测脚本那一版**，不要凭印象优化。

| 本模块 | 评测脚本出处 |
|---|---|
| `decode_bytes` | `run_eval.py:201-208` |
| `html_to_text` | `run_eval.py:197-214`（含 `_SCRIPT_RE` / `_TAG_RE`） |
| `normalize_doc` | `run_eval.py:77-96`（`_MD_NOISE` 与 `nfkc`） |

另外多一个 `normalise_lines`：入库前把正文整理成"行尾无空白、无空行"的形态。
它**只删空白**，而评测的 `normalize_doc` 本来就把空白全去掉，
所以删空白不会让任何 quote 对不上——这一步是为了让"chunk 拼接 == 正文"
这条全覆盖不变式可断言。
"""

from __future__ import annotations

import re
import unicodedata
from html import unescape

# --- 与评测脚本一致的三件套 -------------------------------------------------

#: 出处：run_eval.py:197-198
_SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_HTML_TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)

#: 出处：run_eval.py:77-78
_MD_NOISE = str.maketrans({"*": None, "`": None, "|": None, "#": None,
                           ">": None, "\u200b": None})


def nfkc(text: str) -> str:
    """全角转半角、兼容字符归一。出处：run_eval.py:81-83"""
    return unicodedata.normalize("NFKC", text)


def decode_bytes(raw: bytes) -> tuple[str, str]:
    """先试 UTF-8，失败再试 GB18030（KB-062 是 GBK 导出的旧 OA 文件）。

    出处：run_eval.py:201-208。返回 `(文本, 实际编码名)`——
    编码名要记进 trace，调试时能一眼看出"这篇是怎么读进来的"。
    """
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8(replace)"


def html_to_text(text: str) -> str:
    """剥 `<script>`/`<style>` → 剥全部标签 → 反转义实体。

    出处：run_eval.py:211-214。顺序不能换：先剥 script/style 才能保证
    里面的 `<` 不会把后面的标签切歪；最后才 unescape，否则 `&lt;div&gt;`
    会被当成标签删掉。
    """
    text = _SCRIPT_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    return unescape(text)


def html_title(text: str) -> str:
    """从 `<title>` 取标题，并剥掉 ` - 站点名` 这类后缀。"""
    match = _HTML_TITLE.search(text)
    if not match:
        return ""
    title = unescape(match.group(1).strip())
    return title.split("-")[0].strip() or title


def normalize_doc(text: str) -> str:
    """quote 逐字校验用的规范化。

    出处：run_eval.py:94-96。**只用于 quote 校验与长度计算**，
    不入库、不影响检索打分——检索那边需要保留原文的可读形态。

    NFKC → 去掉所有空白（含零宽空格）→ 去掉 `*` `` ` `` `|` `#` `>`。
    """
    return "".join(nfkc(text).translate(_MD_NOISE).split())


def normalize_text(text: str) -> str:
    """`text_any`/`text_all`/`text_none` 用的子串比较形态。

    出处：run_eval.py:86-91。比 `normalize_doc` 多一步 `casefold()`。
    """
    return "".join(nfkc(text).translate(_MD_NOISE).split()).casefold()


# --- 入库前的正文整理 -------------------------------------------------------

def normalise_lines(text: str) -> str:
    """行尾去空白、丢掉空行。

    **只删空白**是安全的：评测的 `normalize_doc` 会把空白全部去掉，
    所以删多少空白都不会让 quote 对不上。但它让
    "chunks 拼接 == 文档正文" 这条全覆盖不变式可以被断言
    （否则 chunk 边界处的空白差异会让断言永远失败）。
    """
    return "\n".join(line.rstrip() for line in text.splitlines() if line.strip())


def visible_text(raw_text: str, fmt: str) -> tuple[str, str]:
    """文档入库前的统一形态：html 先剥标签，然后整理行。

    返回 `(可见正文, 标题)`——HTML 的标题顺手从 `<title>` 取，
    免得 `load_document` 里再搜一次。
    """
    title = ""
    if fmt == "html":
        title = html_title(raw_text)
        raw_text = html_to_text(raw_text)
    return normalise_lines(raw_text), title
