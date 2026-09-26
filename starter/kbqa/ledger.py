"""Fact Ledger：一次 ``/api/chat`` 生命周期内的**事实权威**。

Generalization Round 2 要建立的两个核心概念之一（另一个是 Finalisation
Authority，落在 ``live.py``）：

* 每一次 live 工具执行都产生一条不可变的 :class:`ToolReceipt`；
  **canonical raw result 是事实来源**，后续任何表示都从它派生、绝不原地修改。
* 至少区分三种表示（任务书 §八）：

  ===========================  =========================================
  Canonical Tool Result        真实工具返回值 —— 事实权威
  Model Projection             发给 DeepSeek 的工具内容 —— 足够完整可推理
  Evidence Projection          最终 ``/api/chat.data_evidence`` —— 必须满足
                               单条 ≤4096 字节、合计 ≤60 个数字
  ===========================  =========================================

  核心不变量：``Model Context Budget != API Evidence Budget``。
  历史缺陷（R2-D1/D2）：``live.py`` 用同一份 evidence 收口结果**覆盖**了
  发给模型的 ``role=tool`` 内容，于是先来的宽查询吃满 60 数字预算，
  后到的精确查询被换成 ``{"truncated": true}`` 的 stub，模型永远看不到真事实。

这里只做"一次请求内的事实登记 + 三种投影"，不引入数据库、不做 event sourcing。
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Optional

from .core.numbers import extract_numbers, numbers_in_object

DATA_SOURCE = "data"
KNOWLEDGE_SOURCE = "knowledge"

#: 单条工具结果进模型上下文的字节上限。
#: **与 API evidence 的 4096 无关**，给得宽松：工具结果本来就不大，
#: 这里只是防极端情况，且用结构化裁剪，绝不把事实换成 stub。
MODEL_RESULT_BYTES = 32 * 1024

#: 评测 evidence_hygiene 的上限（与 ``eval/run_eval.py`` 的常量一致）。
MAX_EVIDENCE_RESULT_BYTES = 4096
MAX_EVIDENCE_NUMBERS = 60


@dataclass(frozen=True)
class ToolReceipt:
    """一次工具执行的不可变记录。

    ``result`` 在构造时**深拷贝**，因此调用方之后怎么改自己的对象都不影响它；
    projection 也一律深拷贝，回不到 canonical。
    """

    receipt_id: str
    tool: str
    params: dict
    result: Any
    source: str = DATA_SOURCE
    order: int = 0

    # -- 派生表示（纯变换，不触碰 self.result） ---------------------------------

    def number_count(self) -> int:
        return len(numbers_in_object(self.result))

    def numbers(self) -> list[float]:
        return numbers_in_object(self.result)

    def is_error(self) -> bool:
        return isinstance(self.result, dict) and "error" in self.result

    def trace_detail(self, limit: int = 600) -> dict:
        blob = json.dumps(self.result, ensure_ascii=False, default=str)
        return {
            "receipt_id": self.receipt_id,
            "tool": self.tool,
            "params": self.params,
            "source": self.source,
            "numbers": len(self.numbers()),
            "result": blob if len(blob) <= limit else blob[:limit] + "…",
        }


class FactLedger:
    """一次请求的 receipt 登记表。数据 receipt 与知识检索 receipt 分开。"""

    def __init__(self) -> None:
        self._receipts: list[ToolReceipt] = []
        self._counters = {DATA_SOURCE: 0, KNOWLEDGE_SOURCE: 0}

    # -- 登记 -------------------------------------------------------------------

    def add(self, tool: str, params: dict, result: Any,
            source: str = DATA_SOURCE) -> ToolReceipt:
        prefix = "D" if source == DATA_SOURCE else "K"
        self._counters[source] = self._counters.get(source, 0) + 1
        receipt = ToolReceipt(
            receipt_id="%s%d" % (prefix, self._counters[source]),
            tool=tool,
            params=copy.deepcopy(params or {}),
            result=copy.deepcopy(result),                    # raw result 一旦登记就冻结
            source=source,
            order=len(self._receipts) + 1,
        )
        self._receipts.append(receipt)
        return receipt

    # -- 读取 -------------------------------------------------------------------

    def all(self) -> list[ToolReceipt]:
        return list(self._receipts)

    def data_receipts(self) -> list[ToolReceipt]:
        """成功的数据库工具 receipt（工具执行失败的不算事实）。"""
        return [r for r in self._receipts if r.source == DATA_SOURCE and not r.is_error()]

    def knowledge_receipts(self) -> list[ToolReceipt]:
        return [r for r in self._receipts if r.source == KNOWLEDGE_SOURCE]

    # -- 三种投影 ---------------------------------------------------------------

    def model_projection(self, receipt: ToolReceipt,
                         max_bytes: int = MODEL_RESULT_BYTES) -> str:
        """给 DeepSeek 的工具内容：完整事实（必要时结构化收缩），绝不用 stub。"""
        obj = _shrink(copy.deepcopy(receipt.result), max_bytes, None)
        if obj is None:                                       # pragma: no cover - 极端兜底
            obj = receipt.result
        return json.dumps(obj, ensure_ascii=False, default=str)

    def evidence_projection(self, receipt: ToolReceipt, answer_numbers: list[float],
                            budget_numbers: int = MAX_EVIDENCE_NUMBERS,
                            budget_bytes: int = MAX_EVIDENCE_RESULT_BYTES):
        """给 ``data_evidence`` 的结果：优先保留 final answer 真正用到的字段。

        返回 ``(projected_result, used_numbers)``；装不下时返回 ``(None, 0)``。
        """
        focused = _focus_on_numbers(copy.deepcopy(receipt.result), answer_numbers)
        shrunk = _shrink(focused, budget_bytes, budget_numbers)
        if shrunk is None:
            return None, 0
        return shrunk, len(numbers_in_object(shrunk))

    def evidence_item(self, receipt: ToolReceipt, projected) -> dict:
        return {
            "tool": receipt.tool,
            "params": receipt.params,
            "result": projected,
            "receipt_id": receipt.receipt_id,
        }


# =========================================================================== 投影实现


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _matches_any(value: float, answer_numbers: list[float]) -> bool:
    return any(abs(value - candidate) <= 0.011 for candidate in answer_numbers)


def _focus_on_numbers(value, answer_numbers: list[float]):
    """只保留"回答真正用到的字段"。

    * 非数字字段（编号、名称、日期、备注）一律保留——它们是出处与上下文；
    * 数字字段只在它的值出现在最终回答里时保留；
    * **一个数字字段都对不上时整棵子树原样保留**——避免把证据削成
      "回答用百分比、证据却需要原始订单数"（H05 的 ``evidence_numbers_any``）。

    纯变换：入参已经是深拷贝，返回值也是新对象。
    """
    if isinstance(value, list):
        return [_focus_on_numbers(item, answer_numbers) for item in value]
    if isinstance(value, dict):
        focused = {key: _focus_on_numbers(item, answer_numbers) for key, item in value.items()}
        numeric_keys = [key for key, item in focused.items() if _is_number(item)]
        if not numeric_keys:
            return focused
        kept = [key for key in numeric_keys if _matches_any(float(focused[key]), answer_numbers)]
        if not kept:
            return focused                                   # 对不上 → 原样保留
        return {key: item for key, item in focused.items()
                if key not in numeric_keys or key in kept}
    return value


def _longest_list_path(value, path: tuple = (), best=None):
    """找当前结构里最长的列表（含嵌套），返回 (长度, 路径)。"""
    if isinstance(value, list):
        if best is None or len(value) > best[0]:
            best = (len(value), path)
        for index, child in enumerate(value):
            best = _longest_list_path(child, path + (index,), best)
    elif isinstance(value, dict):
        for key, child in value.items():
            best = _longest_list_path(child, path + (key,), best)
    return best


def _at(value, path: tuple):
    for key in path:
        value = value[key]
    return value


def _drop_last_key(value) -> bool:
    """没有列表可裁时，删掉最大的 dict 的最后一个键。返回是否删掉了东西。"""
    best = None
    def walk(node, path=()):
        nonlocal best
        if isinstance(node, dict) and len(node) > 1:
            if best is None or len(node) > len(_at(value, best)):
                best = path
        if isinstance(node, dict):
            for key, child in node.items():
                walk(child, path + (key,))
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, path + (index,))
    walk(value)
    if best is None:
        return False
    node = _at(value, best)
    node.pop(list(node.keys())[-1])
    return True


def _fits(value, max_bytes: int, max_numbers: Optional[int]):
    blob = json.dumps(value, ensure_ascii=False, default=str)
    if max_bytes is not None and len(blob.encode("utf-8")) > max_bytes:
        return None
    if max_numbers is not None and len(extract_numbers(blob)) > max_numbers:
        return None
    return len(extract_numbers(blob))


def _shrink(value, max_bytes: int, max_numbers: Optional[int]):
    """结构化收缩：先砍最长列表，再删多余键；始终是合法 JSON。"""
    current = value
    if _fits(current, max_bytes, max_numbers) is not None:
        return current
    for _ in range(4096):
        longest = _longest_list_path(current)
        if longest is not None and longest[0] > 1:
            node = _at(current, longest[1])
            node[:] = node[: max(1, len(node) // 2)]
        elif not _drop_last_key(current):
            return None
        if _fits(current, max_bytes, max_numbers) is not None:
            return current
    return None


# =========================================================================== 证据选择


def _plan_scope(plan) -> dict:
    if plan is None:
        return {}
    return {
        "store_id": getattr(plan, "store_id", None),
        "product_id": getattr(plan, "product_id", None),
        "window": getattr(plan, "window", None),
    }


def _scope_score(receipt: ToolReceipt, scope: dict) -> int:
    """receipt 的参数与最终问题范围的对齐度（generic，不看题号）。"""
    params = receipt.params or {}
    score = 0
    if scope.get("store_id") and params.get("store_id") == scope["store_id"]:
        score += 2
    if scope.get("product_id") and params.get("product_id") == scope["product_id"]:
        score += 2
    window = scope.get("window")
    if window and params.get("start") and params.get("end"):
        if params.get("start") >= window[0] and params.get("end") <= window[1]:
            score += 1                                   # 区间落在问题窗口内
    return score


def _specificity(receipt: ToolReceipt) -> int:
    """参数越具体（带了门店/商品/更窄的区间）越优先。"""
    params = receipt.params or {}
    score = 0
    if params.get("store_id"):
        score += 1
    if params.get("product_id"):
        score += 1
    if params.get("start") and params.get("end"):
        score += 1
    return score


def _coverage(receipt: ToolReceipt, answer_numbers: list[float]) -> set:
    numbers = receipt.numbers()
    return {index for index, value in enumerate(answer_numbers)
            if any(abs(value - candidate) <= 0.011 for candidate in numbers)}


def select_evidence(ledger: FactLedger, answer_numbers: list[float], plan=None,
                    max_numbers: int = MAX_EVIDENCE_NUMBERS,
                    max_bytes: int = MAX_EVIDENCE_RESULT_BYTES) -> list[dict]:
    """挑出**支持最终回答**的最少 receipt 集合（Evidence 不是 Tool Call History）。

    选择信号全部 generic（任务书 §十二）：是否覆盖最终回答的经营数字、
    参数是否与 plan 的 store/product/window 对齐、更具体优先、结果更小优先。
    排序键里含 ``receipt_id``，所以结果与**调用顺序无关**。
    """
    receipts = ledger.data_receipts()
    if not receipts:
        return []
    scope = _plan_scope(plan)

    def rank(receipt: ToolReceipt, weight_coverage: bool = True):
        covered = len(_coverage(receipt, answer_numbers)) if weight_coverage else 0
        return (-covered, -_scope_score(receipt, scope), -_specificity(receipt),
                receipt.number_count(), receipt.receipt_id)

    selected: list[tuple[ToolReceipt, object]] = []
    covered: set = set()
    remaining = max_numbers

    for receipt in sorted(receipts, key=rank):
        new = _coverage(receipt, answer_numbers) - covered
        if not new:
            continue
        projected, used = ledger.evidence_projection(receipt, answer_numbers, remaining)
        if projected is None:
            continue
        selected.append((receipt, projected))
        covered |= new
        remaining -= used

    if not selected:
        # 最终回答里的数字一个都归因不到具体 receipt（例如回答只写百分比、
        # 而契约要求证据里出现原始订单数——H05 的 ``evidence_numbers_any``）。
        # 这时不能空手而归，也不能只赌一条：按优先级把**能装下**的 receipt 都带上，
        # 仍然受 4096/60 预算约束，所以既安全又不至于失控。
        for receipt in sorted(receipts, key=lambda r: rank(r, weight_coverage=False))[:3]:
            projected, used = ledger.evidence_projection(receipt, answer_numbers, remaining)
            if projected is None:
                continue
            selected.append((receipt, projected))
            remaining -= used

    return [ledger.evidence_item(receipt, projected) for receipt, projected in selected]
