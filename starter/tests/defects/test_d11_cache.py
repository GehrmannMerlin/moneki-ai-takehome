"""缺陷 D11：缓存键不含知识库内容（`index.py:23-27`）。

```python
def content_key(kb_dir: Path) -> str:
    digest = hashlib.sha256()
    digest.update(("%s|%s|%s\\n" % (INDEX_VERSION, CHUNKER_VERSION, TOKENIZER_VERSION)).encode())
    return digest.hexdigest()
```

`kb_dir` 只出现在签名里。连带后果：`.cache/index.json` 被提交进仓库（P0 已删），
评委按 README 第 3 步换 `knowledge_base/` 后缓存键不变、`load_index` 命中旧缓存，
服务拿**上一套知识库**答题——评审第 3 步的直接炸点。

`tests/test_p0_infra.py` 里那两条 P0 红测试就是它的复现测试，P2 落地后一起转绿。
这里补的是**端到端**那一层：`load_index` 真的会重建、索引真的跟着知识库变。
"""

from __future__ import annotations

import shutil

from conftest import KB_DIR


def _copy_kb(tmp_path) -> "object":
    target = tmp_path / "kb"
    shutil.copytree(KB_DIR, target)
    return target


def test_key_follows_kb_content(tmp_path):
    """改知识库任一文件 → 缓存键变化。"""
    from kbqa.index import content_key

    kb = _copy_kb(tmp_path)
    before = content_key(kb)
    victim = sorted(kb.rglob("KB-013*"))[0]
    victim.write_text(victim.read_text(encoding="utf-8") + "\n新增一行。\n", encoding="utf-8")
    after = content_key(kb)
    assert before != after, (
        "改了知识库内容，缓存键没变（%s）——评委换库后会拿旧索引答题" % before[:12])


def test_key_follows_file_addition(tmp_path):
    """新增文件 → 缓存键变化。评委的隐藏知识库是"有增有改"的。"""
    from kbqa.index import content_key

    kb = _copy_kb(tmp_path)
    before = content_key(kb)
    (kb / "notices" / "KB-999_新增通知.md").write_text(
        "---\ntitle: 新增通知\nstatus: 现行\n---\n\n正文。\n", encoding="utf-8")
    assert content_key(kb) != before, "新增知识库文件后缓存键没变"


def test_key_follows_file_deletion(tmp_path):
    """删除文件 → 缓存键变化。"""
    from kbqa.index import content_key

    kb = _copy_kb(tmp_path)
    before = content_key(kb)
    sorted(kb.rglob("KB-053*"))[0].unlink()
    assert content_key(kb) != before, "删除知识库文件后缓存键没变"


def test_load_index_rebuilds_on_content_change(tmp_path):
    """端到端：改了知识库之后 `load_index` 必须重建，而不是读回旧索引。"""
    from kbqa.index import load_index

    kb = _copy_kb(tmp_path)
    cache = tmp_path / "index.json"

    first = load_index(kb, cache)
    assert cache.exists(), "索引没落盘"

    # 把一篇文档内容换掉，再看第二次拿到的是不是新内容
    victim = sorted(kb.rglob("KB-042*"))[0]
    victim.write_text(
        "---\ndoc_id: KB-042\ntitle: 改过的总表\nstatus: 现行\n---\n\n"
        "S01 的营业时间改成了 06:00-06:30。\n", encoding="utf-8")

    second = load_index(kb, cache)
    texts = " ".join(chunk.text for chunk in second.chunks)
    assert "06:00-06:30" in texts, (
        "第二次 load_index 读的还是旧缓存（缓存键没跟着内容走）：%s" % second.key[:12])
    assert first.key != second.key


def test_loaded_index_is_usable(tmp_path):
    """缓存命中的索引必须能正常检索（缓存格式不能丢信息）。"""
    from kbqa.index import load_index

    kb = _copy_kb(tmp_path)
    cache = tmp_path / "index.json"
    first = load_index(kb, cache)
    second = load_index(kb, cache)
    assert len(first.chunks) == len(second.chunks)
    scores_a = first.score_terms({"退款": 1.0})
    scores_b = second.score_terms({"退款": 1.0})
    assert scores_a and len(scores_a) == len(scores_b), "缓存读回来的索引检索结果不一致"
