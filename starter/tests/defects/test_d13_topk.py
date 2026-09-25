"""缺陷 D13：先取满 top_k 再过滤已废止版本（`retriever.py:306-307`）。

```python
for score, position in adjusted:
    ...
    hits.append(hit)
    if len(hits) >= top_k:      # ← 先取满 top_k
        break
...
# 取够 top-k 之后，再把过滤掉的那些版本去掉。   ← 再过滤
hits = [hit for hit in hits if hit.doc_id not in excluded]
```

契约 §4 写得很直白："先取前 `top_k` 再做过滤、结果只剩两三条的实现，不符合这一条。"

更糟的是：`_eligible()` 算出来的 `excluded` 从头到尾**没有参与打分**——
`allowed = set(range(len(self.index.chunks)))` 是全量，
所以被排除的文档照样参与排序、照样占掉 top_k 的名额，最后才被删掉。

---

**排查过程中发现的一个额外缺陷（原缺陷清单没有）**：`_eligible()` 的版本判断根本
**从来没生效过**。它读的是 `meta.get("status")`：

```python
if meta.get("status") == "已废止" and ends and as_of.isoformat() >= ends:
```

而 `Document.meta()`（`loader.py:61-76`）写进去的键叫 **`"state"`**：

```python
"state": self.status,
```

`meta.get("status")` 因此恒为 `None`，条件永远不成立——**所有已废止版本
都要参与打分**。实测 `KB-002`/`KB-010`/`KB-012` 三篇已废止文档
在 `as_of=2026-09-01` 下 `_eligible()` 全部返回 `None`（= 合格）。

这是"版本类题（V 系）"的直接杀手，也是"两份同样的政策都进 top-k"的原因。
"""
from __future__ import annotations

import pytest

#: 三篇已废止、且都有接任版本的文档；它们的接任者在 `_effective_to` 里。
DEPRECATED = {"KB-002": "KB-001", "KB-010": "KB-011", "KB-012": "KB-013"}


def test_metadata_exposes_status_under_the_key_the_retriever_reads(index):
    """元数据里必须有 `status` 这个键。

    这是 D13 的根因：`Document.meta()` 写的是 `state`，`_eligible()` 读的是
    `status`，两边对不上，版本过滤整条逻辑失效。
    """
    missing = [doc_id for doc_id, meta in index.docs_meta.items()
               if "status" not in meta and "state" not in meta]
    assert not missing, "这些文档的元数据里既没有 status 也没有 state：%s" % missing

    # 真正的判据：retriever 读哪个键，元数据就得提供哪个键
    sample = index.docs_meta["KB-010"]
    assert "status" in sample, (
        "元数据里没有 'status' 键（只有 %s）——_eligible() 读的正是 meta.get('status')，"
        "它恒为 None，所以版本过滤从来没生效过" % sorted(sample))


def test_deprecated_docs_declare_status(index):
    """三篇已废止文档必须能被识别为已废止。"""
    for doc_id in DEPRECATED:
        meta = index.docs_meta[doc_id]
        value = meta.get("status") or meta.get("state")
        assert value == "已废止", "%s 的状态是 %r，应为 '已废止'" % (doc_id, value)


def test_eligibility_rejects_deprecated_versions_as_of_today(index, today):
    """as_of=今天时，三篇已废止版本都必须被判为不合格。"""
    from kbqa.retriever import Retriever

    retriever = Retriever(index, today)
    still_eligible = [doc_id for doc_id in DEPRECATED
                      if retriever._eligible(doc_id, today, None, False) is None]
    assert not still_eligible, (
        "%s 在 as_of=%s 下仍被判为合格——版本过滤没生效（D13 根因）"
        % ("、".join(still_eligible), today))


def test_deprecated_doc_appears_in_filtered_list(index, today):
    """端到端：过滤结果里必须出现已废止的版本。"""
    from kbqa.retriever import Retriever

    retriever = Retriever(index, today)
    result = retriever.search("会员储值 充值 送", top_k=5, as_of=today)
    filtered_ids = {item["doc_id"] for item in result.filtered}
    assert filtered_ids & set(DEPRECATED), (
        "没有任何已废止文档被过滤（filtered=%s）——版本过滤整条失效" % sorted(filtered_ids))


def test_exactly_topk_after_filtering(index, today):
    """先过滤后截取：结果必须恰好 top_k 条。"""
    from kbqa.retriever import Retriever

    retriever = Retriever(index, today)
    result = retriever.search("会员储值 充值 送", top_k=5, as_of=today)
    assert len(result.hits) == 5, (
        "返回了 %d 条，应为 5 条——先取 top_k 再过滤，空位没人补" % len(result.hits))


@pytest.mark.parametrize("query", ["充值", "退款", "会员储值", "政策", "500"])
def test_exactly_topk_for_policy_queries(index, today, query):
    """政策类查询最容易撞上已废止版本。"""
    from kbqa.retriever import Retriever

    retriever = Retriever(index, today)
    result = retriever.search(query, top_k=5, as_of=today)
    assert len(result.hits) == 5, "%r 只返回 %d 条，应为 5 条" % (query, len(result.hits))


def test_deprecated_doc_ranked_first_is_excluded(index, today):
    """最狠的一种情形：已废止版本分数最高。过滤之后它不能出现在结果里。"""
    from kbqa.retriever import Retriever

    retriever = Retriever(index, today)
    result = retriever.search("会员储值政策 单笔充值", top_k=5, as_of=today)
    leaked = [hit.doc_id for hit in result.hits if hit.doc_id in DEPRECATED]
    assert not leaked, "结果里出现了已废止版本：%s" % leaked


def test_no_filtered_doc_leaks(index, today):
    """通用断言：结果里不能出现被过滤掉的文档。"""
    from kbqa.retriever import Retriever

    retriever = Retriever(index, today)
    for query in ("充值", "退款政策", "会员", "过敏原"):
        result = retriever.search(query, top_k=5, as_of=today)
        excluded = {item["doc_id"] for item in result.filtered}
        leaked = [hit.doc_id for hit in result.hits if hit.doc_id in excluded]
        assert not leaked, "%r 的结果里出现了被过滤的文档：%s" % (query, leaked)


def test_archived_docs_are_not_filtered(index, today):
    """`status=归档` 的文档不参与版本过滤（归档 ≠ 废止，历史周报仍是资料）。"""
    from kbqa.retriever import Retriever

    retriever = Retriever(index, today)
    archived = [doc_id for doc_id, meta in index.docs_meta.items()
                if (meta.get("status") or meta.get("state")) == "归档"]
    assert archived, "索引里没有归档文档，这条测试没有意义"
    for doc_id in archived:
        assert retriever._eligible(doc_id, today, None, False) is None, (
            "%s 是归档文档，不该被过滤掉" % doc_id)


def test_results_are_ranked_descending(index, today):
    """契约 §4：按相关性从高到低排序。"""
    from kbqa.retriever import Retriever

    retriever = Retriever(index, today)
    scores = [hit.score for hit in retriever.search("退款政策", top_k=5).hits]
    assert scores == sorted(scores, reverse=True), "结果没有按分数降序：%s" % scores


def test_enough_eligible_candidates(index, today):
    """前提确认：合格 chunk 足够多，所以"凑不满 top_k"只可能是顺序错。"""
    from kbqa.retriever import Retriever

    retriever = Retriever(index, today)
    result = retriever.search("充值", top_k=5, as_of=today)
    excluded = {item["doc_id"] for item in result.filtered}
    usable = [c for c in index.chunks if c.doc_id not in excluded]
    assert len(usable) >= 5, "合格 chunk 只有 %d 个，凑不满是正常的" % len(usable)
    assert len({c.doc_id for c in usable}) >= 5, "合格文档不足 5 篇（每篇限占一格）"
