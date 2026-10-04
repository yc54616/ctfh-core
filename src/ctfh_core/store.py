from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .types import AttemptState, OperationState, TaskState


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS tasks (
    task_key TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    target_epoch INTEGER NOT NULL DEFAULT 1,
    outcome_json TEXT,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    attempt_id TEXT PRIMARY KEY,
    task_key TEXT NOT NULL REFERENCES tasks(task_key),
    state TEXT NOT NULL,
    owner_label TEXT NOT NULL,
    exit_code INTEGER,
    started_at REAL,
    finished_at REAL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS operations (
    operation_id TEXT PRIMARY KEY,
    task_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    state TEXT NOT NULL,
    result_json TEXT,
    updated_at REAL NOT NULL,
    UNIQUE(task_key, kind, idempotency_key)
);
CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_key TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    path TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(attempt_id, kind, sha256)
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


_ALLOWED: dict[AttemptState, set[AttemptState]] = {
    AttemptState.QUEUED: {AttemptState.STARTING, AttemptState.CANCELLED, AttemptState.LOST},
    AttemptState.STARTING: {AttemptState.RUNNING, AttemptState.CANCELLING, AttemptState.FINISHED, AttemptState.LOST},
    AttemptState.RUNNING: {AttemptState.FINISHED, AttemptState.CANCELLING, AttemptState.LOST},
    AttemptState.CANCELLING: {AttemptState.CANCELLED, AttemptState.LOST},
    AttemptState.FINISHED: set(),
    AttemptState.CANCELLED: set(),
    AttemptState.LOST: set(),
}


class StateStore:
    """Synchronous single-writer host control store."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def recover_live_attempts(self) -> int:
        now = time.time()
        cur = self.conn.execute(
            "UPDATE attempts SET state=?, finished_at=COALESCE(finished_at, ?), updated_at=? "
            "WHERE state IN (?, ?, ?)",
            (
                AttemptState.LOST.value,
                now,
                now,
                AttemptState.STARTING.value,
                AttemptState.RUNNING.value,
                AttemptState.CANCELLING.value,
            ),
        )
        self.conn.commit()
        return cur.rowcount

    def ensure_task(self, task_key: str, target_epoch: int = 1) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO tasks(task_key, state, target_epoch, updated_at) VALUES(?, ?, ?, ?)",
            (task_key, TaskState.OPEN.value, target_epoch, time.time()),
        )
        self.conn.commit()

    def task_target_epoch(self, task_key: str) -> int:
        row = self.conn.execute(
            "SELECT target_epoch FROM tasks WHERE task_key=?", (task_key,)
        ).fetchone()
        if row is None:
            raise KeyError(task_key)
        return int(row["target_epoch"])

    def advance_target_epoch(self, task_key: str) -> int:
        self.conn.execute(
            "UPDATE tasks SET target_epoch=target_epoch+1, updated_at=? WHERE task_key=?",
            (time.time(), task_key),
        )
        self.conn.commit()
        return self.task_target_epoch(task_key)

    def create_attempt(self, attempt_id: str, task_key: str, owner_label: str) -> None:
        self.conn.execute(
            "INSERT INTO attempts(attempt_id, task_key, state, owner_label, updated_at) VALUES(?, ?, ?, ?, ?)",
            (attempt_id, task_key, AttemptState.QUEUED.value, owner_label, time.time()),
        )
        self.conn.commit()

    def attempt_state(self, attempt_id: str) -> AttemptState:
        row = self.conn.execute(
            "SELECT state FROM attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise KeyError(attempt_id)
        return AttemptState(row["state"])

    def transition_attempt(
        self,
        attempt_id: str,
        state: AttemptState,
        *,
        exit_code: int | None = None,
    ) -> None:
        current = self.attempt_state(attempt_id)
        if state not in _ALLOWED[current]:
            raise RuntimeError(f"invalid attempt transition: {current} -> {state}")
        now = time.time()
        started = now if state == AttemptState.RUNNING else None
        finished = now if state in {AttemptState.FINISHED, AttemptState.CANCELLED, AttemptState.LOST} else None
        self.conn.execute(
            "UPDATE attempts SET state=?, exit_code=COALESCE(?, exit_code), "
            "started_at=COALESCE(started_at, ?), finished_at=COALESCE(finished_at, ?), "
            "updated_at=? WHERE attempt_id=?",
            (state.value, exit_code, started, finished, now, attempt_id),
        )
        self.conn.commit()

    def commit_solved(
        self,
        task_key: str,
        *,
        target_epoch: int,
        outcome: dict[str, Any],
    ) -> bool:
        cur = self.conn.execute(
            "UPDATE tasks SET state=?, outcome_json=?, updated_at=? "
            "WHERE task_key=? AND state=? AND target_epoch=?",
            (
                TaskState.SOLVED.value,
                json.dumps(outcome, sort_keys=True),
                time.time(),
                task_key,
                TaskState.OPEN.value,
                target_epoch,
            ),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def record_artifact(
        self,
        task_key: str,
        attempt_id: str,
        kind: str,
        ref: dict[str, object],
    ) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO artifacts(task_key, attempt_id, kind, sha256, path, size, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            (
                task_key,
                attempt_id,
                kind,
                str(ref["sha256"]),
                str(ref["path"]),
                int(ref["size"]),
                time.time(),
            ),
        )
        self.conn.commit()

    def event(self, kind: str, **payload: Any) -> None:
        self.conn.execute(
            "INSERT INTO events(kind, payload_json, created_at) VALUES(?, ?, ?)",
            (kind, json.dumps(payload, sort_keys=True), time.time()),
        )
        self.conn.commit()

    def begin_operation(
        self,
        operation_id: str,
        task_key: str,
        kind: str,
        idempotency_key: str,
    ) -> str:
        row = self.conn.execute(
            "SELECT operation_id FROM operations WHERE task_key=? AND kind=? AND idempotency_key=?",
            (task_key, kind, idempotency_key),
        ).fetchone()
        if row:
            return str(row["operation_id"])
        self.conn.execute(
            "INSERT INTO operations(operation_id, task_key, kind, idempotency_key, state, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (operation_id, task_key, kind, idempotency_key, OperationState.PENDING.value, time.time()),
        )
        self.conn.commit()
        return operation_id

    def set_operation_state(
        self,
        operation_id: str,
        state: OperationState,
        result: Any | None = None,
    ) -> None:
        self.conn.execute(
            "UPDATE operations SET state=?, result_json=?, updated_at=? WHERE operation_id=?",
            (
                state.value,
                None if result is None else json.dumps(result, sort_keys=True),
                time.time(),
                operation_id,
            ),
        )
        self.conn.commit()
