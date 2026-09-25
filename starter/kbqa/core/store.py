"""会话与 trace 的持久化（修 D14）。

starter 的 `sessions.py:16-28`：

```python
def history(self, session_id: Optional[str]) -> list[dict]:
    with self._lock:
        return list(self._turns)          # ← session_id 收了但没用
```

`_turns` 是一个**全局单链表**，所以所有会话共享同一份历史，
`MAX_TURNS = 6` 也是全局共享的。后果：契约 §5 那句"不同 session_id 之间不能串线"
被根本违反，T01/T02/T03 三类追问全部接不上
（第 2 轮「那 7 月呢」被判成"这个会话里没有上文"）。

改成按 `session_id` 分桶，落 SQLite：进程重启不丢、并发安全、调试面板能翻历史。
trace 也从内存版（capacity=200 的 OrderedDict）改成落库——契约 §6 要求
"每答完一题立刻取一次 trace"，落库比内存稳。

**rebuild 不清 traces 表**：它是"回答过程的证据"，跟清洗表没关系。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

#: 一个会话保留最近多少轮。够了解追问，也不至于让槽位被很久以前的话污染。
MAX_TURNS = 12

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    slots      TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS turns (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    question   TEXT,
    answer     TEXT,
    answer_type TEXT,
    slots      TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
CREATE TABLE IF NOT EXISTS traces (
    trace_id   TEXT PRIMARY KEY,
    payload    TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_traces_created ON traces(created_at);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


class SessionStore:
    """按 `session_id` 分桶的对话历史（修 D14）。

    对外形状保持兼容：`history(session_id)` / `append(session_id, turn)` /
    `clear()`，所以 `service.py` 不用改调用方式。
    """

    def __init__(self, db_path: Optional[Path] = None,
                 max_turns: int = MAX_TURNS) -> None:
        self.max_turns = max_turns
        self._lock = threading.Lock()
        self._local = threading.local()
        self.db_path = Path(db_path) if db_path else None
        if self.db_path is None:
            # 没给路径时退化成内存库：测试与单次脚本用得上，
            # 但**仍然是按 session_id 分桶的**，不再有全局共享那条路。
            self._memory = sqlite3.connect(":memory:", check_same_thread=False)
            self._memory.row_factory = sqlite3.Row
            self._memory.executescript(_SCHEMA)
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        if self.db_path is None:
            return self._memory
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    # -- 会话 -------------------------------------------------------------------

    def history(self, session_id: Optional[str]) -> list[dict]:
        """**严格按 `session_id` 过滤**（这就是 D14 的修复点）。"""
        if not session_id:
            return []
        conn = self._connect()
        with self._lock:
            rows = conn.execute(
                "SELECT question, answer, answer_type, slots, created_at "
                "FROM turns WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (str(session_id), self.max_turns),
            ).fetchall()
        turns = []
        for row in reversed(rows):                        # 还原成正序
            turn = {
                "question": row["question"],
                "answer": row["answer"],
                "answer_type": row["answer_type"],
                "created_at": row["created_at"],
            }
            if row["slots"]:
                try:
                    turn["slots"] = json.loads(row["slots"])
                except ValueError:                        # pragma: no cover
                    turn["slots"] = {}
            turns.append(turn)
        return turns

    def append(self, session_id: Optional[str], turn: dict) -> None:
        if not session_id:
            return
        conn = self._connect()
        with self._lock:
            conn.execute(
                "INSERT INTO turns (session_id, question, answer, answer_type, slots, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (
                    str(session_id),
                    turn.get("question") or "",
                    turn.get("answer") or "",
                    turn.get("answer_type") or "",
                    json.dumps(turn.get("slots") or {}, ensure_ascii=False),
                    _now(),
                ),
            )
            conn.commit()

    def slots(self, session_id: Optional[str]) -> dict:
        """这个会话上一次解析出来的槽位（追问继承的起点）。"""
        if not session_id:
            return {}
        conn = self._connect()
        with self._lock:
            row = conn.execute(
                "SELECT slots FROM sessions WHERE session_id = ?", (str(session_id),)
            ).fetchone()
        if not row or not row["slots"]:
            return {}
        try:
            return json.loads(row["slots"])
        except ValueError:                                # pragma: no cover
            return {}

    def save_slots(self, session_id: Optional[str], slots: dict) -> None:
        if not session_id:
            return
        conn = self._connect()
        with self._lock:
            conn.execute(
                "INSERT INTO sessions (session_id, slots, updated_at) VALUES (?,?,?)"
                " ON CONFLICT(session_id) DO UPDATE SET slots = excluded.slots,"
                " updated_at = excluded.updated_at",
                (str(session_id), json.dumps(slots or {}, ensure_ascii=False), _now()),
            )
            conn.commit()

    def clear(self, session_id: Optional[str] = None) -> None:
        conn = self._connect()
        with self._lock:
            if session_id:
                conn.execute("DELETE FROM turns WHERE session_id = ?", (str(session_id),))
                conn.execute("DELETE FROM sessions WHERE session_id = ?", (str(session_id),))
            else:
                conn.execute("DELETE FROM turns")
                conn.execute("DELETE FROM sessions")
            conn.commit()


class TraceStore:
    """trace 落 SQLite（修 D15 的一半：内存版重启就丢、满 200 条挤掉旧的）。"""

    def __init__(self, db_path: Optional[Path] = None, capacity: int = 2000) -> None:
        self.capacity = capacity
        self._lock = threading.Lock()
        self._local = threading.local()
        self._counter = 0
        self.db_path = Path(db_path) if db_path else None
        if self.db_path is None:
            self._memory = sqlite3.connect(":memory:", check_same_thread=False)
            self._memory.row_factory = sqlite3.Row
            self._memory.executescript(_SCHEMA)
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        if self.db_path is None:
            return self._memory
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def new_id(self, today: str) -> str:
        with self._lock:
            self._counter += 1
            return "t-%s-%04d" % (today.replace("-", ""), self._counter)

    def save(self, trace) -> None:
        payload = trace.as_dict() if hasattr(trace, "as_dict") else dict(trace)
        conn = self._connect()
        with self._lock:
            conn.execute(
                "INSERT INTO traces (trace_id, payload, created_at) VALUES (?,?,?)"
                " ON CONFLICT(trace_id) DO UPDATE SET payload = excluded.payload",
                (payload.get("trace_id"), json.dumps(payload, ensure_ascii=False), _now()),
            )
            # 只保留最近 capacity 条，别让 trace 库无限长
            conn.execute(
                "DELETE FROM traces WHERE trace_id NOT IN "
                "(SELECT trace_id FROM traces ORDER BY created_at DESC LIMIT ?)",
                (self.capacity,),
            )
            conn.commit()

    def get(self, trace_id: str) -> Optional[dict]:
        conn = self._connect()
        with self._lock:
            row = conn.execute(
                "SELECT payload FROM traces WHERE trace_id = ?", (trace_id,)
            ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["payload"])
        except ValueError:                                # pragma: no cover
            return None

    def recent(self, limit: int = 20) -> list[dict]:
        """调试面板用：列出最近的 trace。"""
        conn = self._connect()
        with self._lock:
            rows = conn.execute(
                "SELECT payload FROM traces ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        out: list[Any] = []
        for row in rows:
            try:
                out.append(json.loads(row["payload"]))
            except ValueError:                            # pragma: no cover
                continue
        return out
