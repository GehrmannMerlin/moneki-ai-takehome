"""清洗表之上的只读数据工具。

这些工具的 SQL 形状沿用 starter（`tools.py` 的 GROUP BY / JOIN 结构本来就是对的），
只把三处**口径**改对：

1. 区间一律闭区间（原来 `date < end`，丢末日整天）—— 现在统一走 `MetricsEngine`；
2. 五指标走 v3 现行口径（原来退款行被排除、`orders` 数明细行、`refund_amount` 恒 0）；
3. `run_sql` 那条可写通道整个移除。P3 会以"只读 SQL 闸"的形态重新引入
   （`SELECT`/`WITH` 开头 + 必须有 `FROM` + `mode=ro` 连接）。

只读不是靠"上层不发写语句"，而是靠连接模式（`MetricsEngine.open_readonly` 的 `mode=ro`）。
"""

from __future__ import annotations

from typing import Any, Optional

from .metrics import Caliber, MetricsEngine, round2
from .normalize import yuan

__all__ = ["DataTools"]


class DataTools:
    """指标、支付构成、榜单、门店与品类聚合、单价核对、两期对比。全部只读。"""

    def __init__(self, engine: MetricsEngine) -> None:
        self.engine = engine

    # -- 基础设施 ---------------------------------------------------------------

    @property
    def conn(self):
        return self.engine.conn

    def close(self) -> None:
        self.engine.close()

    # -- 元信息 -----------------------------------------------------------------

    def cleaning_report(self) -> dict:
        return self.engine.cleaning_report()

    def valid_sales_rows(self, caliber: Caliber | str = Caliber.V3) -> int:
        return self.engine.valid_sales_rows(caliber)

    def stores(self) -> list[dict]:
        return self.engine.stores()

    def products(self) -> list[dict]:
        return self.engine.products()

    def data_period(self) -> dict:
        return self.engine.data_period()

    # -- 指标 -------------------------------------------------------------------

    def query_metrics(self, start: str, end: str, store_id=None, product_id=None,
                      caliber: Caliber | str = Caliber.V3) -> dict:
        """五指标汇总。`/api/metrics/summary` 与问答链路共用这一个实现。"""
        return self.engine.summary(start, end, store_id, product_id, caliber)

    def daily_metrics(self, start: str, end: str, store_id=None, product_id=None,
                      caliber: Caliber | str = Caliber.V3) -> dict:
        """逐日指标，区间内每天必有一条。"""
        return self.engine.daily(start, end, store_id, product_id, caliber)

    # -- 更细的维度 ---------------------------------------------------------------

    def payment_mix(self, start: str, end: str, store_id=None,
                    caliber: Caliber | str = Caliber.V3) -> dict:
        """各支付方式的订单数、金额与占比。

        H05 考的就是这个：占比算子由代码做，原始指标（现金订单数、当天总订单数）
        必须能在 `data_evidence.result` 里找到。
        """
        caliber = Caliber.of(caliber)
        where, params = self.engine._where(start, end, store_id, None, caliber, alias="c")
        sql = """
            SELECT c.payment AS payment,
                   COUNT(DISTINCT CASE WHEN c.is_refund = 0 THEN c.order_id END) AS orders,
                   COALESCE(SUM(CASE WHEN c.is_refund = 1 THEN -c.qty ELSE c.qty END), 0) AS qty,
                   COALESCE(SUM(c.amount_cents), 0) AS net_cents
            FROM clean_lines c
            WHERE {where}
            GROUP BY c.payment
        """.format(where=where)
        rows = list(self.conn.execute(sql, params))

        total = self.query_metrics(start, end, store_id, caliber=caliber)
        total_orders = total["orders"] or 0
        total_net = total["net_revenue"] or 0.0
        payments: dict[str, dict] = {}
        for row in rows:
            orders = int(row["orders"] or 0)
            net = yuan(int(row["net_cents"] or 0))
            payments[row["payment"]] = {
                "orders": orders,
                "qty": int(row["qty"] or 0),
                "net_revenue": net,
                "share_orders": round(orders / total_orders, 6) if total_orders else 0.0,
                "share_revenue": round(net / total_net, 6) if total_net else 0.0,
            }
        return {
            "start": start,
            "end": end,
            "store_id": store_id,
            "total_orders": total_orders,
            "total_net_revenue": total_net,
            "payments": payments,
        }

    def top_products(self, start: str, end: str, store_id=None, limit: int = 10,
                     caliber: Caliber | str = Caliber.V3) -> dict:
        """商品排行榜，按净营业额降序。"""
        caliber = Caliber.of(caliber)
        where, params = self.engine._where(start, end, store_id, None, caliber, alias="c")
        sql = """
            SELECT c.product_id AS product_id, p.product_name AS product_name,
                   p.product_category AS product_category,
                   COALESCE(SUM(c.amount_cents), 0) AS net_cents,
                   COUNT(DISTINCT CASE WHEN c.is_refund = 0 THEN c.order_id END) AS orders,
                   COALESCE(SUM(CASE WHEN c.is_refund = 1 THEN -c.qty ELSE c.qty END), 0) AS qty
            FROM clean_lines c
            LEFT JOIN products p ON p.product_id = c.product_id
            WHERE {where}
            GROUP BY c.product_id
            ORDER BY net_cents DESC
        """.format(where=where)
        items = [{
            "product_id": row["product_id"],
            "product_name": row["product_name"],
            "product_category": row["product_category"],
            "net_revenue": yuan(int(row["net_cents"] or 0)),
            "orders": int(row["orders"] or 0),
            "qty": int(row["qty"] or 0),
        } for row in self.conn.execute(sql, params)]
        return {"start": start, "end": end, "store_id": store_id,
                "products": items[: max(1, int(limit or 10))]}

    def by_store(self, start: str, end: str, product_id=None,
                 caliber: Caliber | str = Caliber.V3) -> dict:
        """按门店拆的五指标，净营业额降序。"""
        stores = []
        for store in self.stores():
            metrics = self.query_metrics(start, end, store["store_id"], product_id, caliber)
            metrics.update(store_name=store["store_name"], category=store["category"],
                           district=store["district"])
            stores.append(metrics)
        stores.sort(key=lambda item: item["net_revenue"], reverse=True)
        return {"start": start, "end": end, "stores": stores}

    def by_store_category(self, start: str, end: str,
                          caliber: Caliber | str = Caliber.V3) -> dict:
        """按门店品类（`stores.category`）汇总。

        D02「哪个品类的门店净营业额最高」用它，答案里要出现品类名。
        """
        groups: dict[str, dict] = {}
        for store in self.stores():
            metrics = self.query_metrics(start, end, store["store_id"], caliber=caliber)
            bucket = groups.setdefault(store["category"], {
                "category": store["category"],
                "stores": [],
                "net_revenue": 0.0,
                "refund_amount": 0.0,
                "orders": 0,
                "qty": 0,
            })
            bucket["stores"].append(store["store_id"])
            bucket["net_revenue"] = round(bucket["net_revenue"] + metrics["net_revenue"], 2)
            bucket["refund_amount"] = round(bucket["refund_amount"] + metrics["refund_amount"], 2)
            bucket["orders"] += metrics["orders"]
            bucket["qty"] += metrics["qty"]
        for bucket in groups.values():
            bucket["aov"] = (round2(bucket["net_revenue"] / bucket["orders"])
                             if bucket["orders"] else None)
        items = sorted(groups.values(), key=lambda item: item["net_revenue"], reverse=True)
        return {"start": start, "end": end, "categories": items}

    def first_sale_date(self, product_id: str) -> Optional[str]:
        """某商品的首次销售日期。

        H03「冷萃乌龙茶上市第一个月」要先锚定首销日再算窗口（KB-028 说 8 月上市）。
        """
        row = self.conn.execute(
            "SELECT MIN(date) FROM clean_lines "
            "WHERE product_id = ? AND reject_v3 IS NULL AND is_refund = 0",
            (str(product_id or "").strip().upper(),),
        ).fetchone()
        return row[0] if row and row[0] else None

    def unit_price_check(self, product_id: str, start: str, end: str, store_id=None) -> dict:
        """实收单价 vs 维表建档价（KB-001 §5.3 的"建档价滞后"）。

        同一天不同门店可能卖不同的价（活动只在一家店做），所以按门店也拆一份。
        H04/T03 要用它说明"建档价 42、现价 45"这件事。
        """
        product_id = str(product_id or "").strip().upper()
        params: list[Any] = [product_id, start, end]
        clause = ""
        if store_id:
            clause = " AND store_id = ?"
            params.append(str(store_id).strip().upper())
        rows = list(self.conn.execute(
            "SELECT date, amount_cents, qty, store_id FROM clean_lines "
            "WHERE product_id = ? AND reject_v3 IS NULL AND is_refund = 0 AND qty > 0 "
            "AND date >= ? AND date <= ?%s ORDER BY date" % clause,
            params,
        ))
        observed: dict[str, int] = {}
        by_store: dict[str, dict[str, int]] = {}
        latest_price = None
        latest_date = None
        for row in rows:
            qty = int(row["qty"] or 0)
            if qty <= 0:                                   # pragma: no cover - SQL 已滤
                continue
            price = yuan(int(round(int(row["amount_cents"] or 0) / qty)))
            key = "%.2f" % price
            observed[key] = observed.get(key, 0) + 1
            bucket = by_store.setdefault(row["store_id"], {})
            bucket[key] = bucket.get(key, 0) + 1
            if latest_date is None or row["date"] >= latest_date:
                latest_date, latest_price = row["date"], price

        table_price = None
        row = self.conn.execute(
            "SELECT unit_price FROM products WHERE product_id = ?", (product_id,)).fetchone()
        if row:
            table_price = float(row[0])
        return {
            "product_id": product_id,
            "start": start,
            "end": end,
            "store_id": store_id,
            "observed_unit_prices": observed,
            "by_store": by_store if len(observed) > 1 else {},
            "rows": len(rows),
            "latest_price": latest_price,
            "latest_date": latest_date,
            "table_unit_price": table_price,
            "table_price_is_stale": (table_price is not None and latest_price is not None
                                     and abs(table_price - latest_price) > 0.005),
        }

    def compare_periods(self, start_a: str, end_a: str, start_b: str, end_b: str,
                        store_id=None, product_id=None,
                        caliber: Caliber | str = Caliber.V3) -> dict:
        """比较两个区间的五指标：B 相对 A 的涨跌。

        差额用**舍入后**的指标相减（D04/T01 的 0.17 = 36.53 − 36.36 就是这么来的）：
        先各自 `ROUND_HALF_UP` 到两位，再相减，而不是相减后再舍入。
        """
        first = self.query_metrics(start_a, end_a, store_id, product_id, caliber)
        second = self.query_metrics(start_b, end_b, store_id, product_id, caliber)
        deltas: dict[str, dict] = {}
        for field in ("net_revenue", "refund_amount", "orders", "aov", "qty"):
            before, after = first.get(field), second.get(field)
            if before is None or after is None:
                deltas[field] = {"delta": None, "pct": None, "direction": "未知"}
                continue
            delta = round(after - before, 2)
            deltas[field] = {
                "delta": delta,
                "pct": round(delta / before * 100, 2) if before else None,
                "direction": "涨" if delta > 0 else ("跌" if delta < 0 else "持平"),
                "from": before,
                "to": after,
            }
        return {"period_a": first, "period_b": second, "delta": deltas}
