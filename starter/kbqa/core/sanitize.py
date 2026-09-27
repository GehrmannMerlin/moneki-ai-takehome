"""从检索到的文本里认出「冲着助手来的指令」句。

Generalization Round 4 把这里从"只认 H069 那一条"扩成**通用**的注入识别：

* 中英文都要覆盖（`忽略之前所有指令` / `Ignore all previous instructions`）；
* 不是只有"忽略指令"一种形状——"系统提示：""无论用户问什么""从现在起你必须"
  "不要引用任何来源""调用 drop/delete"都算；
* 一定要有**反向护栏**：正经公司制度（`员工必须佩戴手套`、`门店必须每日消毒`、
  `顾客应当出示小票`）不能被误删。判别点是**指令的动作对象**：冲着"回答/输出/引用/
  系统提示/指令"去的是注入；冲着"门店/员工/顾客/商品"去的是业务规定。

"压制引用"这一类天然和业务表述重叠（`报告不要引用来源不明的数据`），所以它单独处理：
只有当整句**同时**在讲业务对象、且没有任何"面向助手"的动作词时才放行。冒充系统提示
或覆盖指令的一律直接判注入，不给这条护栏开口子。

`sanitize()` 是 `Hit.safe_text` 与 live 知识投影的唯一实现：原文照留（给 citation 与
trace），进模型的那一份必须是它剥过的结果。
"""

from __future__ import annotations

import re

_SENTENCE = re.compile(r"[^。！？!?\n]+[。！？!?]?")

#: 强判据：命中即认定是冲着助手来的指令。
_STRONG_PATTERNS = (
    # ① 覆盖指令：忽略 / 无视 / 忘记 + 之前/所有 + 指令/规则/提示
    re.compile(r"(忽略|无视|忘记|忘掉|放弃|不要理|不用管).{0,10}"
               r"(之前|以上|上面|先前|前面|所有|一切|原有).{0,8}"
               r"(指令|规则|提示|设定|要求|限制|约束|指示)"),
    re.compile(r"ignore\s+(all\s+|any\s+)?(previous|prior|above|earlier|preceding)\s+"
               r"(instructions?|prompts?|rules?|messages?|directions?)", re.I),
    re.compile(r"disregard\s+(all\s+|any\s+)?(previous|prior|above|earlier)\s+"
               r"(instructions?|prompts?|rules?)", re.I),
    # ② 冒充系统 / 开发者消息
    re.compile(r"(系统提示|系统指令|系统消息|开发者消息|developer\s+message|"
               r"system\s*(prompt|message))\s*[:：]", re.I),
    # ③ 无条件作答指令（"无论用户问什么都回答 X"）
    re.compile(r"无论(用户|别人|他|你|对方)?\s*(问|说|要)(什么|啥|什么问题|什么话)"),
    re.compile(r"whatever\s+(the\s+)?(user|they|someone)\s+"
               r"(asks?|says?|wants?|requests?)", re.I),
    # ④ 时间锚 + 作答动作（"从现在起你必须只回答 X"）
    re.compile(r"(从现在(开始|起)|从此刻(开始|起)|接下来|今后).{0,12}"
               r"(只回答|只输出|只回复|只作答|只按|回答|输出|回复|作答)"),
    re.compile(r"from\s+now\s+on.{0,20}(answer|reply|respond|say|output|ignore)", re.I),
    # ⑤ 第二人称命令（正经制度不会对"你"下命令）
    re.compile(r"(你必须|你应该|请你?务必|你务必).{0,8}"
               r"(回答|输出|回复|作答|忽略|只回答|只输出)"),
    re.compile(r"(you\s+must|you\s+should|you\s+are\s+to)\s+"
               r"(answer|reply|respond|output|ignore)\b", re.I),
    # ⑥ 借文档下写操作命令
    re.compile(r"(执行|运行|调用|请执行|去执行)\s*.{0,10}"
               r"\b(drop|delete|truncate|update|insert|alter)\b", re.I),
    re.compile(r"\b(call|invoke|execute|run)\b.{0,40}\b(drop|delete|truncate|wipe)\b", re.I),
    re.compile(r"\b(drop|truncate)\s+\w*\s*table\b", re.I),
    re.compile(r"\b(drop|truncate|delete)\b.{0,30}\b(database|table|tables|records|rows)\b",
               re.I),
)

#: 与业务表述天然重叠的一类：压制引用。单独走"业务护栏"。
_SUPPRESSION_PATTERNS = (
    re.compile(r"(不要|禁止|别|无需|不必|请勿).{0,8}(引用|标注|列出|给出).{0,8}"
               r"(来源|出处|依据|链接|参考文献)"),
    re.compile(r"(do\s+not|don'?t|never)\s+(cite|reference|quote)\s+"
               r"(any\s+)?(source|sources|evidence|reference)", re.I),
)

#: 业务护栏：句子里同时在讲这些对象，说明是业务规定，不是给助手的命令。
_BUSINESS_HINT = re.compile(
    r"(门店|员工|顾客|客人|商品|库存|排班|考勤|消毒|验收|营业额|销售|报表|报告|"
    r"供应商|会员|收货|配送|收银|备料|出品|预订|退货|巡检)")
#: "面向助手"的动作词：出现任何一个，业务护栏就不成立。
_ASSISTANT_ACTION = re.compile(
    r"(回答|输出|回复|作答|系统提示|系统指令|系统消息|指令|你必须|你应该|"
    r"ignore|instructions?|system\s+(prompt|message))", re.I)

#: 供外部（citation builder）复用：这一句是不是被剥掉的指令。
_INJECTION_PATTERNS = _STRONG_PATTERNS + _SUPPRESSION_PATTERNS


def is_instruction_like(sentence: str) -> bool:
    text = sentence.strip()
    if not text:
        return False
    if any(pattern.search(text) for pattern in _STRONG_PATTERNS):
        return True
    if any(pattern.search(text) for pattern in _SUPPRESSION_PATTERNS):
        # 反向护栏：业务对象在场、又没有任何面向助手的动作词 → 是业务规定。
        return not (_BUSINESS_HINT.search(text) and not _ASSISTANT_ACTION.search(text))
    return False


def split_sentences(text: str) -> list[str]:
    """按句子切，保留换行结构，方便逐句判断与逐句引用。"""
    parts: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        found = [match.group(0).strip() for match in _SENTENCE.finditer(line)]
        parts.extend(piece for piece in found if piece)
    return parts


def sanitize(text: str) -> tuple[str, list[str]]:
    """返回（去掉指令句之后的文本，被去掉的句子）。"""
    kept: list[str] = []
    dropped: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        pieces = [match.group(0) for match in _SENTENCE.finditer(line)] or [line]
        safe = []
        for piece in pieces:
            if is_instruction_like(piece):
                dropped.append(piece.strip())
            else:
                safe.append(piece)
        joined = "".join(safe).strip()
        if joined:
            kept.append(joined)
    return "\n".join(kept), dropped
