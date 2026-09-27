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
MAX_SESSIONS = 500

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    slots      TEXT,
    updated_at TEXT,
    epoch      TEXT,
    access_seq INTEGER DEFAULT 0
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


def _ensure_column(conn: sqlite3.Connection, table: str, name: str, definition: str) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)}
    if name not in columns:
        conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, name, definition))


class SessionStore:
    """按 `session_id` 分桶的对话历史（修 D14）。

    对外形状保持兼容：`history(session_id)` / `append(session_id, turn)` /
    `clear()`，所以 `service.py` 不用改调用方式。
    """

    def __init__(self, db_path: Optional[Path] = None,
                 max_turns: int = MAX_TURNS,
                 max_sessions: int = MAX_SESSIONS) -> None:
        self.max_turns = max_turns
        self.max_sessions = max_sessions
        self._lock = threading.Lock()
        self._local = threading.local()
        self.db_path = Path(db_path) if db_path else None
        if self.db_path is None:
            # 没给路径时退化成内存库：测试与单次脚本用得上，
            # 但**仍然是按 session_id 分桶的**，不再有全局共享那条路。
            self._memory = sqlite3.connect(":memory:", check_same_thread=False)
            self._memory.row_factory = sqlite3.Row
            self._memory.executescript(_SCHEMA)
            _ensure_column(self._memory, "sessions", "epoch", "TEXT")
            _ensure_column(self._memory, "sessions", "access_seq", "INTEGER DEFAULT 0")
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
                _ensure_column(conn, "sessions", "epoch", "TEXT")
                _ensure_column(conn, "sessions", "access_seq", "INTEGER DEFAULT 0")

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
        self.append_with_state(session_id, turn, None)

    def append_with_state(self, session_id: Optional[str], turn: dict, state=None) -> None:
        """Atomically persist a turn, optional semantic state, and retention."""
        if not session_id:
            return
        conn = self._connect()
        with self._lock:
            try:
                conn.execute("BEGIN")
                sid = str(session_id)
                if state is not None:
                    payload = state.to_dict() if hasattr(state, "to_dict") else dict(state)
                    self._upsert_session(conn, sid, payload, payload.get("epoch"))
                else:
                    self._touch_session(conn, sid)
                conn.execute(
                    "INSERT INTO turns (session_id, question, answer, answer_type, slots, created_at)"
                    " VALUES (?,?,?,?,?,?)",
                    (
                        sid,
                        turn.get("question") or "",
                        turn.get("answer") or "",
                        turn.get("answer_type") or "",
                        json.dumps(turn.get("slots") or {}, ensure_ascii=False),
                        _now(),
                    ),
                )
                self._prune_turns(conn, sid)
                self._evict_lru(conn)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

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
            self._upsert_session(conn, str(session_id), slots or {}, None)
            self._evict_lru(conn)
            conn.commit()

    def load_state(self, session_id: Optional[str], epoch: str = ""):
        """Load normalized state; a mismatched epoch returns an empty state."""
        from ..conversation import ConversationState

        if not session_id:
            return ConversationState.empty(epoch)
        conn = self._connect()
        with self._lock:
            row = conn.execute(
                "SELECT slots, epoch FROM sessions WHERE session_id = ?",
                (str(session_id),),
            ).fetchone()
            if row:
                self._touch_session(conn, str(session_id))
                conn.commit()
        if not row or not row["slots"]:
            return ConversationState.empty(epoch)
        try:
            payload = json.loads(row["slots"])
        except (TypeError, ValueError):
            return ConversationState.empty(epoch)
        stored_epoch = str(row["epoch"] or payload.get("epoch") or "")
        # Legacy `save_slots` rows have no epoch and cannot be trusted against
        # a live dataset/KB context.  Treat them as stale when the caller has
        # an active epoch instead of silently importing old entities.
        if epoch and stored_epoch != epoch:
            return ConversationState.empty(epoch, "context_epoch_changed")
        return ConversationState.from_dict(payload, epoch=epoch or stored_epoch)

    def save_state(self, session_id: Optional[str], state) -> None:
        if not session_id:
            return
        payload = state.to_dict() if hasattr(state, "to_dict") else dict(state)
        conn = self._connect()
        with self._lock:
            self._upsert_session(conn, str(session_id), payload, payload.get("epoch"))
            self._evict_lru(conn)
            conn.commit()

    def _next_access_seq(self, conn: sqlite3.Connection) -> int:
        return int(conn.execute("SELECT COALESCE(MAX(access_seq), 0) + 1 FROM sessions").fetchone()[0])

    def _touch_session(self, conn: sqlite3.Connection, session_id: str) -> None:
        seq = self._next_access_seq(conn)
        conn.execute(
            "INSERT INTO sessions (session_id, slots, updated_at, epoch, access_seq) VALUES (?,?,?,?,?)"
            " ON CONFLICT(session_id) DO UPDATE SET updated_at = excluded.updated_at,"
            " access_seq = excluded.access_seq",
            (session_id, None, _now(), None, seq),
        )

    def _upsert_session(self, conn: sqlite3.Connection, session_id: str,
                        payload: dict, epoch: Optional[str]) -> None:
        seq = self._next_access_seq(conn)
        encoded = json.dumps(payload or {}, ensure_ascii=False)
        conn.execute(
            "INSERT INTO sessions (session_id, slots, updated_at, epoch, access_seq) VALUES (?,?,?,?,?)"
            " ON CONFLICT(session_id) DO UPDATE SET slots = excluded.slots,"
            " updated_at = excluded.updated_at, epoch = excluded.epoch,"
            " access_seq = excluded.access_seq",
            (session_id, encoded, _now(), epoch, seq),
        )

    def _prune_turns(self, conn: sqlite3.Connection, session_id: str) -> None:
        if self.max_turns <= 0:
            conn.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
            return
        conn.execute(
            "DELETE FROM turns WHERE session_id = ? AND id NOT IN "
            "(SELECT id FROM turns WHERE session_id = ? ORDER BY id DESC LIMIT ?)",
            (session_id, session_id, int(self.max_turns)),
        )

    def _evict_lru(self, conn: sqlite3.Connection) -> None:
        limit = max(1, int(self.max_sessions))
        rows = conn.execute(
            "SELECT session_id FROM sessions ORDER BY access_seq DESC LIMIT -1 OFFSET ?",
            (limit,),
        ).fetchall()
        for row in rows:
            session_id = row["session_id"]
            conn.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))

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
