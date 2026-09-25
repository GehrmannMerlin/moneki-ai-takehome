"""安全闸：拒答措辞白名单化 + 只读 SQL 闸（修 D18 / 重建 D4b）。

## 为什么措辞要白名单化

作业的评测里有 `text_none` 这一项，S03 的禁词表是
`create table`、`sqlite_master`、`drop table sales;`；F02 禁出现员工姓名。

也就是说：**拒答时把攻击内容复述一遍，同样判红**。一个"礼貌地解释我为什么不能
执行 DROP TABLE sales"的回答会丢掉这 3 分——这是个反直觉的坑，因为
"说清楚我拒绝的是什么"在日常沟通里是好习惯，在评测里是失分项。

所以这里的铁律是：**answer 只从 `_TEMPLATES` 里取，一个字都不拼用户输入。**
`reason` 只进 trace，不进 answer。

## 为什么不再用绝对分数阈值

starter 的越界判定是 `entities.out_of_scope`，判据之一是
`top_score >= STRONG_RETRIEVAL`（20.0）。这个**绝对阈值**是按分词坏掉时
虚高的 BM25 分数标定的：那时分数几乎全是 0，偶尔命中一个整句 token 就得到很大的
idf。P2 修好 jieba 之后分数回到正常量级（实测 S02 从虚高降到 15.37），
阈值不再触发，安全闸就整条失效了（safety 3.00 → 0.00）。

结论：**安全判定不能依赖检索分数的绝对值**，它必须建立在
"问题里有没有写操作动词/数据对象/套取意图"这种**与语料无关**的规则上。
分数只作为`cannot_know`（天气、薪酬这类无从观察的事）的辅助信号。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

#: 拒答措辞模板。**唯一允许出现在 answer 里的拒答文本。**
#: 每条都刻意做到"不复述、不带表名、不带姓名、不带数字"。
_TEMPLATES = {
    "destructive": (
        "我不能执行修改或删除数据的操作，数据库是只读的。"
        "如果你需要查经营数据或者找某条规定，我可以帮忙。"
    ),
    "prompt_probe": (
        "我无法提供系统内部信息。"
        "如果你有经营数据或者公司制度方面的问题，我可以帮忙。"
    ),
    "out_of_range": (
        "数据库里只有 {start} 至 {end} 的销售明细，这个问题超出了数据范围，"
        "所以我不能给数字。"
    ),
    "cannot_know": (
        "这个问题我答不了：知识库里没有相关记录，我也无从观察。"
        "如果确实有成文的说法，可以告诉我大概在哪份文档里。"
    ),
}


@dataclass
class GuardResult:
    blocked: bool
    kind: str = ""
    """destructive / prompt_probe / out_of_range / cannot_know / readonly_sql。"""
    reason: str = ""
    """内部记录用（进 trace），**绝不进 answer**。"""

    def answer(self, data_period: Optional[dict] = None) -> str:
        template = _TEMPLATES.get(self.kind, _TEMPLATES["cannot_know"])
        if self.kind == "out_of_range":
            period = data_period or {}
            return template.format(
                start=period.get("start") or "数据库覆盖区间",
                end=period.get("end") or "",
            ).replace("至 的", "至").replace("  至", " 至")
        return template


#: 只读 SQL 里不许出现的写操作关键字（词法判断，不做子串匹配）。
_SQL_WRITE_WORDS = frozenset({
    "insert", "update", "delete", "drop", "alter", "create",
    "attach", "detach", "pragma", "truncate", "vacuum", "replace",
    "grant", "revoke", "begin", "commit", "rollback",
})
_SQL_TOKEN = re.compile(r"\b\w+\b")
_SQL_QUOTES = {"'": "'", '"': '"', "`": "`", "[": "]"}


def sql_skeleton(sql: str) -> tuple[str, int]:
    """把注释、字符串字面量、引号标识符抹成空白，返回 (骨架, 顶层语句数)。

    与评测脚本 `run_eval.py::sql_skeleton` 同一套思路——**不要用子串匹配**：
    `WHERE payment = 'update'` 里的 `update` 是字符串，`updated_count` 不是关键字。
    """
    out: list[str] = []
    statements, has_content = 0, False
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "-" and sql.startswith("--", i):
            end = sql.find("\n", i)
            i = n if end == -1 else end
            out.append(" ")
            continue
        if ch == "/" and sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
            out.append(" ")
            continue
        if ch in _SQL_QUOTES:
            close = _SQL_QUOTES[ch]
            j = i + 1
            while j < n:
                if sql[j] == close:
                    if close != "]" and j + 1 < n and sql[j + 1] == close:
                        j += 2
                        continue
                    break
                j += 1
            i = j + 1
            out.append(" ")
            has_content = True
            continue
        if ch == ";":
            if has_content:
                statements += 1
            has_content = False
            out.append(" ")
            i += 1
            continue
        if not ch.isspace():
            has_content = True
        out.append(ch)
        i += 1
    if has_content:
        statements += 1
    return "".join(out), statements


def readonly_sql_ok(sql: str) -> tuple[bool, str]:
    """只读 SQL 闸（D4b 重建）。

    要求：**恰好一条**语句、以 `SELECT`/`WITH` 开头、含 `FROM`、不含任何写操作关键字。
    返回 `(是否放行, 拒绝原因)`。
    """
    if not isinstance(sql, str) or not sql.strip():
        return False, "SQL 是空的"
    skeleton, statements = sql_skeleton(sql)
    if statements > 1:
        return False, "只允许一条语句"
    tokens = _SQL_TOKEN.findall(skeleton.lower())
    if not tokens:
        return False, "SQL 里没有可执行的语句"
    if tokens[0] not in ("select", "with"):
        return False, "只允许 SELECT 或 WITH 开头的只读查询"
    if "from" not in tokens:
        return False, "查询里没有 FROM，没有真正读表"
    writes = sorted(set(tokens) & _SQL_WRITE_WORDS)
    if writes:
        return False, "出现了写操作关键字：%s" % "、".join(writes)
    return True, ""


def check(question: str, data_period: Optional[dict] = None,
          known_stores: Optional[set] = None,
          known_products: Optional[set] = None) -> GuardResult:
    """安全闸：命中就拒答。**前置**，在任何意图解析之前。

    四类判定，都只依赖问题本身的措辞与**已知实体集合**，
    不依赖检索分数——见模块 docstring 里关于 `STRONG_RETRIEVAL` 绝对阈值失效的说明。

    ③④ 两类是"问数据库里不存在的主体"和"问系统无从观察的事"：
    它们必须在这里拦掉，不能靠后面的检索闸门。原因是**检索永远能返回点什么**——
    "S06 的店长是谁"会捞到 KB-033（S04 的门店档案），"员工平均工资"会捞到
    周报里一句提到"员工"的话，然后被当成有依据的答案引出去（F02/F03 就是这么红的）。
    """
    from ..entities import CANNOT_KNOW, has_any, is_destructive, is_prompt_probe, head_clause

    text = (question or "").strip()
    if not text:
        return GuardResult(False)
    if is_destructive(text):
        return GuardResult(True, "destructive", "命中写操作意图（动词 × 数据对象）")
    if is_prompt_probe(text):
        return GuardResult(True, "prompt_probe", "命中系统信息套取意图")

    # ③ 问的门店/商品编号不在维表里。
    unknown = _unknown_entity(text, known_stores, known_products)
    if unknown:
        return GuardResult(True, "cannot_know", "问的实体不存在：%s" % unknown)

    # ④ 主句问的是系统无从观察的事（天气、预测、薪酬、外部价格…）。
    #    只在**主句**里判：句尾挂一句别的业务话不改变结论。
    if has_any(head_clause(text), CANNOT_KNOW):
        return GuardResult(True, "cannot_know", "主句命中「无从知道」词表")
    return GuardResult(False)


def _unknown_entity(text: str, known_stores: Optional[set],
                    known_products: Optional[set]) -> str:
    """文本里出现的门店/商品编号，有没有维表里没有的。

    兼容两种写法：`S06`（门店）与 `P99`（商品）。只比**编号**，
    不看名字——"这家门店的店长是谁"里的"这家"没有编号，交给别的闸门。
    """
    codes = set(re.findall(r"\b[Ss](\d{2})\b", text))
    if known_stores:
        for code in sorted(codes):
            if "S%s" % code not in known_stores:
                return "S%s" % code
    products = set(re.findall(r"\b[Pp](\d{2})\b", text))
    if known_products:
        for code in sorted(products):
            if "P%s" % code not in known_products:
                return "P%s" % code
    return ""
