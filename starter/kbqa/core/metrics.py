"""口径引擎：v3 现行（默认）/ v2 可选。

**这是全项目唯一的指标计算出口。** `/api/metrics/*`、看板的图表与排行榜、
`/api/chat` 的 `data_evidence` 三处都调它，所以"接口与问答口径一致"不是靠纪律，
是靠只有一处实现。

口径依据：KB-001 v3 §4（指标定义）、§5（数字冲突）。
KB-002（v2）已废止，只在用户明确问"当时的规定"时才用，差异三处：

| 项 | v2（KB-002） | v3（KB-001，默认） |
|---|---|---|
| 退款行 | 剔除 | **计入净营业额**（退款按自身日期归属） |
| `amount` 为空 | 回填 `qty × unit_price` | **直接剔除，不回填** |
| 客单价分母 | 明细行数 | **有效订单数**（销售行 `DISTINCT order_id`） |

五指标（v3）：
* `net_revenue`  = 区间内全部行的 `amount_cents` 之和（含退款行）
* `refund_amount` = 退款行金额的绝对值之和
* `orders`       = 销售行的 `DISTINCT order_id` 计数
* `aov`          = `net_revenue ÷ orders`，`ROUND_HALF_UP` 两位（不是银行家舍入）
* `qty`          = 销售 qty − 退款 qty

区间一律**闭区间**（`date >= start AND date <= end`），契约 §2 与 KB-001 §4 都这样要求。
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from .cleaning import V2_REFUND_EXCLUDED

METRIC_FIELDS = ("net_revenue", "refund_amount", "orders", "aov", "qty")


class Caliber(str, Enum):
    """口径版本。默认 v3（KB-001 现行）。"""

    V3 = "v3"
    V2 = "v2"

    @classmethod
    def of(cls, value: "Caliber | str | None") -> "Caliber":
        if isinstance(value, Caliber):
            return value
        if not value:
            return cls.V3
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            return cls.V3


def round2(value) -> float:
    """四舍五入保留两位（KB-001 §4 的客单价口径，`ROUND_HALF_UP` 不是银行家舍入）。"""
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def yuan(cents) -> float:
    """分转元。金额全程以整数"分"参与计算，只在出口转元。"""
    return float(Decimal(int(cents or 0)) / 100)


def open_readonly(path: Path) -> sqlite3.Connection:
    """以只读模式打开清洗表。

    用 `mode=ro` 而不是"约定上层不发写语句"：契约要求数据库**不能有任何改动**，
    这条保证必须落在连接模式上。P1 起 `run_sql` 那条可写通道已整个移除。
    """
    conn = sqlite3.connect("file:%s?mode=ro" % Path(path).as_posix(), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


class MetricsEngine:
    """清洗表之上的口径引擎。每个线程各持一条只读连接。

    SQLite 连接绑定线程，而 FastAPI 的同步接口跑在线程池里，所以用 thread-local。
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._local = threading.local()

    # -- 基础设施 ---------------------------------------------------------------

    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = open_readonly(self.db_path)
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # -- 口径相关的 SQL 片段 -----------------------------------------------------

    @staticmethod
    def reject_column(caliber: Caliber) -> str:
        """该口径下"保留行"的判定列。"""
        return "reject_v2" if caliber is Caliber.V2 else "reject_v3"

    @staticmethod
    def net_expr(caliber: Caliber, alias: str = "") -> str:
        """净营业额的金额表达式。

        v3：`amount_cents` 原样（退款行是负数，自然冲减净额）。
        v2：把 v3 剔掉、但 v2 能按 `qty × unit_price` 回填的行补回来。
            回填用的是 `products.unit_price`，所以 v2 的查询要 LEFT JOIN products。
        """
        prefix = "%s." % alias if alias else ""
        column = "%samount_cents" % prefix
        if caliber is not Caliber.V2:
            return "COALESCE(%s, 0)" % column
        return ("COALESCE(%s, CAST(ROUND(%sqty * p.unit_price) AS INTEGER))"
                % (column, prefix))

    @classmethod
    def _keep_clause(cls, caliber: Caliber, alias: str = "") -> str:
        """保留行的 WHERE 片段。

        v2 里 `reject_v2` 为 NULL 就是保留行（退款行与回填不了的空金额行已标上原因）；
        v3 里 `reject_v3` 为 NULL 就是保留行。
        """
        prefix = "%s." % alias if alias else ""
        return "%s%s IS NULL" % (prefix, cls.reject_column(caliber))

    def _where(
        self,
        start: str,
        end: str,
        store_id: Optional[str] = None,
        product_id: Optional[str] = None,
        caliber: Caliber = Caliber.V3,
        alias: str = "",
    ) -> tuple[str, list[Any]]:
        """构造 WHERE。**闭区间**：末日的数据必须算进来（修 D2）。"""
        prefix = "%s." % alias if alias else ""
        clause = ["%sdate >= ?" % prefix, "%sdate <= ?" % prefix, self._keep_clause(caliber, alias)]
        params: list[Any] = [start, end]
        if store_id:
            clause.append("%sstore_id = ?" % prefix)
            params.append(str(store_id).strip().upper())
        if product_id:
            clause.append("%sproduct_id = ?" % prefix)
            params.append(str(product_id).strip().upper())
        return " AND ".join(clause), params

    @staticmethod
    def _needs_products_join(caliber: Caliber) -> bool:
        """只有 v2 需要 JOIN products（回填要用建档价）。"""
        return caliber is Caliber.V2

    # -- 指标 -------------------------------------------------------------------

    def summary(
        self,
        start: str,
        end: str,
        store_id: Optional[str] = None,
        product_id: Optional[str] = None,
        caliber: Caliber | str = Caliber.V3,
    ) -> dict:
        """五指标。空区间返回数值 0 与 `aov=None`，不报错（契约 §2）。"""
        caliber = Caliber.of(caliber)
        join = ("LEFT JOIN products p ON p.product_id = c.product_id"
                if self._needs_products_join(caliber) else "")
        where, params = self._where(start, end, store_id, product_id, caliber, alias="c")
        sql = """
            SELECT
                COALESCE(SUM({net}), 0)                                        AS net_cents,
                COALESCE(SUM(CASE WHEN c.is_refund = 1 THEN -{net} ELSE 0 END), 0) AS refund_cents,
                COUNT(DISTINCT CASE WHEN c.is_refund = 0 THEN c.order_id END)  AS orders,
                COALESCE(SUM(CASE WHEN c.is_refund = 1 THEN -c.qty ELSE c.qty END), 0) AS qty
            FROM clean_lines c {join}
            WHERE {where}
        """.format(net=self.net_expr(caliber, "c"), join=join, where=where)
        row = self.conn.execute(sql, params).fetchone()

        net_cents = int(row["net_cents"] or 0)
        refund_cents = int(row["refund_cents"] or 0)
        orders = int(row["orders"] or 0)
        qty = int(row["qty"] or 0)
        if caliber is Caliber.V2:
            # v2 不把退款计入，退款金额恒为 0（KB-002 的口径）
            refund_cents = 0
        return {
            "start": start,
            "end": end,
            "store_id": store_id,
            "product_id": product_id,
            "net_revenue": yuan(net_cents),
            "refund_amount": yuan(refund_cents),
            "orders": orders,
            "aov": round2(Decimal(net_cents) / 100 / orders) if orders else None,
            "qty": qty,
        }

    def daily(
        self,
        start: str,
        end: str,
        store_id: Optional[str] = None,
        product_id: Optional[str] = None,
        caliber: Caliber | str = Caliber.V3,
    ) -> dict:
        """逐日指标。区间内**每一天**都要有一条记录，没有营业额的日期补 0（契约 §3）。"""
        caliber = Caliber.of(caliber)
        join = ("LEFT JOIN products p ON p.product_id = c.product_id"
                if self._needs_products_join(caliber) else "")
        where, params = self._where(start, end, store_id, product_id, caliber, alias="c")
        sql = """
            SELECT c.date AS date,
                   COALESCE(SUM({net}), 0) AS net_cents,
                   COUNT(DISTINCT CASE WHEN c.is_refund = 0 THEN c.order_id END) AS orders
            FROM clean_lines c {join}
            WHERE {where}
            GROUP BY c.date
        """.format(net=self.net_expr(caliber, "c"), join=join, where=where)
        found = {row["date"]: (int(row["net_cents"] or 0), int(row["orders"] or 0))
                 for row in self.conn.execute(sql, params)}

        days = []
        cursor = date.fromisoformat(start)
        last = date.fromisoformat(end)
        while cursor <= last:
            key = cursor.isoformat()
            net_cents, orders = found.get(key, (0, 0))
            days.append({
                "date": key,
                "net_revenue": yuan(net_cents),
                "orders": orders,
                "aov": round2(Decimal(net_cents) / 100 / orders) if orders else None,
            })
            cursor += timedelta(days=1)
        return {"days": days}

    # -- 元信息 -----------------------------------------------------------------

    def valid_sales_rows(self, caliber: Caliber | str = Caliber.V3) -> int:
        """按该口径清洗后保留的明细行数（销售行 + 退款行）。契约 §1 的 `valid_sales_rows`。"""
        caliber = Caliber.of(caliber)
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM clean_lines WHERE %s" % self._keep_clause(caliber)).fetchone()[0])

    def cleaning_report(self) -> dict:
        """清洗台账：原始行数 + 六项剔除原因分布（数据质量面板直接用）。"""
        import json

        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = 'cleaning_report'").fetchone()
        if not row:
            return {}
        try:
            return json.loads(row[0])
        except ValueError:                                # pragma: no cover
            return {}

    def data_period(self) -> dict:
        """数据实际覆盖的区间。必须是干净 ISO 日期——它是 P3 区间闸的判据来源。"""
        row = self.conn.execute(
            "SELECT MIN(date), MAX(date) FROM clean_lines WHERE reject_v3 IS NULL").fetchone()
        return {"start": row[0] or "", "end": row[1] or ""}

    def stores(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM stores ORDER BY store_id")]

    def products(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM products ORDER BY product_id")]

    def unit_prices(self) -> dict[str, float]:
        """建档价（`products.unit_price`）。注意 KB-001 §5.3：它是月底更新的，
        商品**现行售价**要以调价通知为准，这里只用于 v2 回填与"建档价滞后"的对比。"""
        return {r["product_id"]: r["unit_price"]
                for r in self.conn.execute("SELECT product_id, unit_price FROM products")}
