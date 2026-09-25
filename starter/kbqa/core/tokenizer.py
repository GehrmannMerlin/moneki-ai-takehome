"""jieba 分词。修 starter 的 D6（`tokenizer.py:20-22`）。

starter 是 `normalise(text).split()`——中文没有空格，所以整句变成一个 token，
BM25 只有"查询串与文档里出现完全相同整句"时才可能命中。这是"检索答非所问"
的总根源，也是 HANDOVER 那句"检索命中率 95%"在数学上不可能成立的原因。

两件事要注意：

1. **别名词典要挂进 jieba 的自定义词典**，否则 `牛肉poke` 会被切成
   `牛肉` + `poke`、`Makai Poke` 被切成 `makai` + `poke`，
   别名匹配的整词判定就失效了。
2. **jieba 首次加载词典约 0.5 秒**，必须放在服务启动期（rebuild 与 `Service()`
   各预热一次），不能等第一个请求——契约对答题耗时有要求。
"""

from __future__ import annotations

import re
import unicodedata

#: 分词规则变了，索引缓存必须失效。
TOKENIZER_VERSION = "tokenizer-3"

#: 中文里几乎不携带信息的字。只用在"查询覆盖率"上，索引照常保留全部词。
STOP_CHARS = frozenset("的了吗呢是在有和与及或就都也还把被给对从向于个些这那哪什么怎样如何多少几请帮我你他它可以能要想会一下少吧啊呀们么样过得着为所")
STOP_WORDS = frozenset("the a an of to in is are and or for on at it this that how what".split())

#: 纯标点/符号的 token 直接丢掉，它们对 BM25 只有噪声。
_PUNCT = re.compile(r"^[\W_]+$", re.U)

_initialised = False


def normalise(text: str) -> str:
    """全角转半角、统一大小写，比较与分词都走这一层。"""
    return unicodedata.normalize("NFKC", text or "").lower()


def init_jieba(extra_words=()) -> None:
    """预热 jieba 并把别名词典里的写法加进自定义词典。

    `extra_words` 传别名表里的全部写法（含数据库写法），
    保证 `牛肉poke` / `味噌拉面` / `Makai Poke` 不被切碎。

    **调用顺序很重要**：必须先把词典挂完，再让任何文本走 `tokenize()`。
    jieba 的 `add_word` 是**全局且累积**的——每加一个词都会改变**之后**
    所有文本的切分结果。所以"先切一段、再加词、再切一段"会让两段文本
    按不同词典切分，索引也就不可复现了（实测踩到过：
    先建索引得到 `哪`/`种`，再建一次得到 `哪种`）。
    """
    global _initialised
    import jieba

    for word in extra_words or ():
        word = (word or "").strip()
        if len(word) >= 2:
            jieba.add_word(word, freq=100000)
    if not _initialised:
        # 触发词典加载（0.5 秒左右），把它挪到启动期而不是首个请求
        jieba.lcut("预热")
        _initialised = True


def prime_from_texts(texts) -> None:
    """把一批文本切一遍，把切出来的词回灌进 jieba 词典。

    **当前没有在用**（`build_index` 不再调它），保留下来是为了写清楚为什么不能用：
    jieba 的 `add_word` 会改变后续切分，于是"扫正文→加词→再扫"是一个
    正反馈：第一遍得到 `哪`/`种`，回灌之后第二遍得到 `哪种`，再加进去，
    第三遍又不一样。实测连跑三次 postings 规模是 2918 / 2919 / 2922，不收敛。

    正确的做法是**不想让分词可复现，而是让索引不依赖分词状态**：
    把切分结果冻结进 `Chunk.tokens` 随索引落盘（见 `core/chunker.py`）。
    词典只挂别名词典（KB-003 那种成文登记），不扫正文。
    """
    import jieba

    seen: set[str] = set()
    for text in texts or ():
        for token in jieba.lcut(normalise(text)):
            token = token.strip()
            if len(token) >= 2 and not _PUNCT.match(token):
                seen.add(token)
    for token in seen:
        jieba.add_word(token, freq=100000)


def tokenize(text: str) -> list[str]:
    """jieba 分词，丢纯标点。

    返回的是**规范化之后**（NFKC + lower）的 token——文档侧与查询侧
    走同一条路径，这是能对上的前提。
    """
    import jieba

    normalized = normalise(text)
    if not normalized.strip():
        return []
    tokens = []
    for token in jieba.lcut(normalized):
        token = token.strip()
        if not token or _PUNCT.match(token):
            continue
        tokens.append(token)
    return tokens


def content_tokens(text: str) -> list[str]:
    """去掉虚词之后的查询词，用来算"这个问题被文档覆盖了多少"。"""
    kept = []
    for token in tokenize(text):
        if token in STOP_WORDS:
            continue
        if all(char in STOP_CHARS for char in token):
            continue
        kept.append(token)
    return kept


def ngrams(text: str, size: int = 2) -> list[str]:
    """字符 n-gram，作为分词的兜底召回（别名切不出来时还能靠它撞上）。"""
    normalized = re.sub(r"\s+", "", normalise(text))
    if len(normalized) < size:
        return []
    return [normalized[i:i + size] for i in range(len(normalized) - size + 1)]
