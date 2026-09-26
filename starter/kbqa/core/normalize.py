"""规范化原语：编号、日期、金额、数量。

全部是纯函数、无 IO、无全局状态——清洗层、口径引擎与数据工具共用同一套，
避免"同一个字符串在两个地方被解析成两样"。

口径依据：KB-001 v3 §2（数据规范化）。
"""

from __future__ import annotations

import math
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

#: 金额里的货币符号与各种空白，去掉之后再按 Decimal 解析。
_CURRENCY = str.maketrans("", "", "¥￥ \t\u3000")

#: `YYYY-MM-DD` 与 `YYYY/M/D`：年在前。
_YMD = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$")
#: `DD-MM-YYYY`：**日在前**（KB-001 §2.2 明确）。
_DMY = re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$")
#: `YYYY/MM/DD` 用斜杠的写法也归到 _YMD。
_YMD_SLASH = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$")


def norm_id(value: object) -> str:
    """编号规范化：trim + upper。门店、商品、order_id、支付方式都走这里。

    必须先规范化再判脏外键，否则 `' s99 '` 这种带空格的脏值会漏网。
    """
    if value is None:
        return ""
    return str(value).strip().upper()


def parse_date(value: object) -> Optional[date]:
    """把三种写法的日期解析成 `datetime.date`，解析不了返回 None。

    支持：`YYYY-MM-DD`、`YYYY/M/D`、`DD-MM-YYYY`（日在前）。

    **必须过 `date()` 构造校验**：`'2026-13-45'` 能通过正则但日历上不存在，
    只靠正则解析会把它留下来，`valid_sales_rows` 就变成 18293 而不是 18290。

    返回 None 即触发剔除规则 1（`1_unparseable_date`）。
    空字符串与 `'N/A'` 同样返回 None。
    """
    text = "" if value is None else str(value).strip()
    if not text:
        return None

    match = _YMD.match(text) or _YMD_SLASH.match(text)
    if match:
        year, month, day = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    else:
        match = _DMY.match(text)
        if not match:
            return None
        # 日在前：第 1 组是日、第 2 组是月、第 3 组是年
        year, month, day = (int(match.group(3)), int(match.group(2)), int(match.group(1)))

    try:
        return date(year, month, day)
    except ValueError:
        return None                      # 格式合法但日历非法，例如 2026-13-45


def parse_amount_cents(value: object) -> tuple[Optional[int], str]:
    """金额解析成"分"，返回 `(cents, status)`，status ∈ {ok, empty, bad}。

    KB-001 v3 §2.3 / §3.2：`¥38.00` 与 `38.00` 是同一个金额；
    **空金额直接剔除，不回填**（v2 才回填 `qty × unit_price`）。

    status 不是 `ok` 就触发剔除规则 2（`2_empty_amount`）。
    """
    text = ("" if value is None else str(value)).translate(_CURRENCY).strip()
    if not text:
        return None, "empty"
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None, "bad"
    if not number.is_finite():
        return None, "bad"
    try:
        return int((number * 100).to_integral_value()), "ok"
    except (InvalidOperation, OverflowError, ValueError):   # pragma: no cover
        return None, "bad"


def parse_qty(value: object) -> Optional[int]:
    """数量按**严格整数**语义解析（KB-001 §2.4：`qty` 按整数解析）。

    * `"3"` / `3` → 3；
    * `"3.0"` → 3（整数值，Decimal 上与 3 相等，不是截断）；
    * `"1.5"` / `"2.7"` → None——**真正的小数不允许被 `int()` 静默截断成 1/2**，
      解析失败即进入清洗剔除规则 3（qty 无效）。
    * None 或解析出的整数 `<= 0` 同样触发剔除规则 3。
    """
    text = ("" if value is None else str(value)).strip()
    if not text:
        return None
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    if number != number.to_integral_value():
        return None                      # 真正的小数：不是整数，不能截断
    try:
        return int(number)
    except (InvalidOperation, OverflowError, ValueError):    # pragma: no cover
        return None


def yuan(cents: Optional[int]) -> float:
    """分转元。金额一律以"分"为整数参与计算，只在出口处转成元。"""
    if cents is None:
        return 0.0
    return float(Decimal(int(cents)) / 100)


def is_finite_number(value: object) -> bool:
    """给 JSON 序列化用：NaN / Infinity 不是合法 JSON 数值。"""
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
