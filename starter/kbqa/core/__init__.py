"""P1 数据层：清洗 + 口径引擎 + 只读数据工具。

分层：

* `normalize`  —— 规范化原语（编号/日期/金额/数量），纯函数、无 IO
* `cleaning`   —— 六条剔除规则 + 建 clean.db（含守恒自验）
* `metrics`    —— 口径引擎（v3 现行 / v2 可选），只读连接
* `datatools`  —— 清洗表之上的只读查询工具

`/api/metrics/*`、看板、`/api/chat` 的 `data_evidence` 三处共用同一个口径引擎，
这是"接口与问答口径一致"的根源保证。
"""

from __future__ import annotations

__all__ = ["normalize", "cleaning", "metrics", "datatools"]
