"""缺陷 D8：一律按 UTF-8 解码且 `errors="ignore"`（`loader.py:82-84`）。

```python
def decode_bytes(raw, path, warnings):
    \"\"\"统一按 UTF-8 读。个别老文件里有怪字符，忽略掉就行，不影响检索。\"\"\"
    return raw.decode("utf-8", errors="ignore")
```

KB-062 是从旧 OA 系统导出的 **GBK** 文件。`errors="ignore"` 会把无法解码的字节
**直接丢掉**——不是报错，是静默产生一篇残缺的乱码文档。注释里"不影响检索"这句话
本身就是错的：整个中文正文都被丢了。

评测脚本 `run_eval.py:201-208` 的 `decode_bytes` 是"先 UTF-8，失败再 GB18030"，
两边不一致正是 quote 逐字校验必挂的原因之一。
"""

from __future__ import annotations

from conftest import KB_DIR

KB062 = KB_DIR / "legacy" / "KB-062_旧OA导出_营业时间调整通知.txt"


def _doc(documents, doc_id: str):
    return next((d for d in documents if d.doc_id == doc_id), None)


def test_kb062_file_really_is_gbk():
    """前提确认：这个文件确实不是 UTF-8（先证伪"文件本身没问题"这个假设）。"""
    raw = KB062.read_bytes()
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    else:
        raise AssertionError("KB-062 现在是合法 UTF-8 了，这条测试的前提不再成立")
    assert "23:00" in raw.decode("gb18030")


def test_kb062_loads_without_mojibake(documents):
    """KB-062 的正文必须能读出正常中文。"""
    doc = _doc(documents, "KB-062")
    assert doc is not None, "KB-062 没有进索引"
    assert "营业时间" in doc.text, (
        "KB-062 正文里读不到 '营业时间'，实际开头是：%r" % doc.text[:80])


def test_kb062_contains_the_answer(documents):
    """字节丢掉之后，"23:00"这个金标答案就没了。"""
    doc = _doc(documents, "KB-062")
    assert doc is not None
    assert "23:00" in doc.text, (
        "KB-062 正文里找不到 '23:00'——GBK 字节被 errors='ignore' 丢掉了")


def test_kb062_has_no_replacement_chars(documents):
    """不该出现 U+FFFD 或大段控制字符。"""
    doc = _doc(documents, "KB-062")
    assert doc is not None
    assert "\ufffd" not in doc.text, "KB-062 正文里出现了替换字符 U+FFFD（解码失败）"


def test_kb062_records_encoding(documents):
    """解码方式要能记下来（trace/调试要用）。"""
    doc = _doc(documents, "KB-062")
    assert doc is not None
    assert getattr(doc, "encoding", "").lower().startswith("gb"), (
        "KB-062 的解码方式记成了 %r，应为 gb18030 之类"
        % getattr(doc, "encoding", None))


def test_utf8_docs_unaffected(documents):
    """回归：UTF-8 的文档不能因为降级逻辑而读坏。"""
    doc = _doc(documents, "KB-001")
    assert doc is not None and "净营业额" in doc.text
