"""Durable, private progress journal for one browser-driven CTF contest."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

from .live_events import EVENT_KINDS, EVENT_LIMIT, public_text


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VERDICTS = {"accepted", "rejected", "unknown"}
_OUTCOMES = {"progress", "idle", "needs_human"}

_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS contest (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    site_url TEXT NOT NULL,
    account_label TEXT NOT NULL,
    auth_entry TEXT,
    browser_session TEXT NOT NULL,
    thread_id TEXT,
    model TEXT,
    reasoning_effort TEXT,
    cyber_access_program TEXT,
    last_note TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'active',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS challenges (
    challenge_key TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('open', 'solved', 'unknown')),
    site_evidence TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions (
    challenge_key TEXT NOT NULL REFERENCES challenges(challenge_key),
    flag_sha256 TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('submitting', 'accepted', 'rejected', 'unknown')),
    site_evidence TEXT NOT NULL DEFAULT '',
    observed_url TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (challenge_key, flag_sha256)
);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state TEXT NOT NULL CHECK (state IN ('running', 'finished', 'failed', 'interrupted')),
    outcome TEXT,
    error_kind TEXT,
    started_at REAL NOT NULL,
    finished_at REAL,
    input_tokens INTEGER,
    cached_input_tokens INTEGER,
    output_tokens INTEGER
);
CREATE TABLE IF NOT EXISTS challenge_work (
    challenge_key TEXT PRIMARY KEY REFERENCES challenges(challenge_key),
    note TEXT NOT NULL DEFAULT '',
    next_action TEXT NOT NULL DEFAULT '',
    retry_at REAL NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS live_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    turn_id INTEGER REFERENCES turns(id),
    challenge_key TEXT,
    role TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS live_events_challenge ON live_events(challenge_key, id);
"""


def validate_site_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("site URL must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("site URL must not contain credentials")
    if parsed.fragment:
        raise ValueError("site URL must not contain a fragment")
    return value


class LiveStore:
    """One contest per state directory; no credentials or raw flags are stored."""

    def __init__(self, state_dir: Path, *, create: bool = True):
        self.state_dir = state_dir.expanduser().resolve()
        if not create and not (self.state_dir / "contest.sqlite3").is_file():
            raise ValueError("no live contest exists in this state directory")
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.state_dir, 0o700)
        path = self.state_dir / "contest.sqlite3"
        self.conn = sqlite3.connect(path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        os.chmod(path, 0o600)
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript(_SCHEMA)
        columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(contest)")
        }
        for name in ("model", "reasoning_effort", "cyber_access_program", "agent_command", "engine"):
            if name not in columns:
                self.conn.execute(f"ALTER TABLE contest ADD COLUMN {name} TEXT")
        turn_columns = {str(row["name"]) for row in self.conn.execute("PRAGMA table_info(turns)")}
        if "challenge_key" not in turn_columns:
            self.conn.execute("ALTER TABLE turns ADD COLUMN challenge_key TEXT")
        if "role" not in turn_columns:
            self.conn.execute("ALTER TABLE turns ADD COLUMN role TEXT NOT NULL DEFAULT 'legacy'")
        for name in ("input_tokens", "cached_input_tokens", "output_tokens"):
            if name not in turn_columns:
                self.conn.execute(f"ALTER TABLE turns ADD COLUMN {name} INTEGER")
        work_columns = {str(row["name"]) for row in self.conn.execute("PRAGMA table_info(challenge_work)")}
        if "thread_id" not in work_columns:
            self.conn.execute("ALTER TABLE challenge_work ADD COLUMN thread_id TEXT")
        challenge_columns = {str(row["name"]) for row in self.conn.execute("PRAGMA table_info(challenges)")}
        if "solved_source" not in challenge_columns:
            self.conn.execute("ALTER TABLE challenges ADD COLUMN solved_source TEXT")
        submission_columns = {str(row["name"]) for row in self.conn.execute("PRAGMA table_info(submissions)")}
        if "resolved_by_solve" not in submission_columns:
            self.conn.execute("ALTER TABLE submissions ADD COLUMN resolved_by_solve INTEGER NOT NULL DEFAULT 0")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> LiveStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def ensure_contest(
        self,
        site_url: str | None,
        account_label: str | None,
        auth_entry: str | None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        cyber_access_program: str | None = None,
        agent_command: tuple[str, ...] | None = None,
    ) -> dict[str, object]:
        if agent_command is not None and (
            not agent_command or not all(isinstance(arg, str) and arg for arg in agent_command)
        ):
            raise ValueError("agent command must be a nonempty argv array")
        encoded_command = json.dumps(agent_command) if agent_command is not None else None
        if cyber_access_program not in {None, "standard", "daybreak_blue", "daybreak_red"}:
            raise ValueError("unsupported cyber access program")
        if reasoning_effort not in {None, "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}:
            raise ValueError("unsupported reasoning effort")
        row = self.conn.execute("SELECT * FROM contest WHERE id=1").fetchone()
        if row is None:
            if not site_url:
                raise ValueError("site URL is required for a new live contest")
            site_url = validate_site_url(site_url)
            label = account_label or "default"
            session_key = hashlib.sha256(
                f"{site_url}\0{label}\0{self.state_dir}".encode()
            ).hexdigest()[:16]
            now = time.time()
            self.conn.execute(
                "INSERT INTO contest(id, site_url, account_label, auth_entry, browser_session, "
                "model, reasoning_effort, cyber_access_program, agent_command, created_at, updated_at) "
                "VALUES(1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (site_url, label, auth_entry, f"ctfh-{session_key}", model,
                 reasoning_effort, cyber_access_program, encoded_command, now, now),
            )
            self.conn.commit()
        else:
            if site_url is not None and validate_site_url(site_url) != row["site_url"]:
                raise ValueError("state directory belongs to a different CTF site")
            if account_label is not None and account_label != row["account_label"]:
                raise ValueError("state directory belongs to a different account label")
            if auth_entry is not None and auth_entry != row["auth_entry"]:
                if row["auth_entry"] is not None:
                    raise ValueError("auth entry differs from the saved contest")
                self.conn.execute("UPDATE contest SET auth_entry=? WHERE id=1", (auth_entry,))
                self.conn.commit()
            if encoded_command is not None and encoded_command != row["agent_command"]:
                if row["thread_id"] or self.conn.execute(
                    "SELECT 1 FROM challenge_work WHERE thread_id IS NOT NULL LIMIT 1"
                ).fetchone():
                    raise ValueError("agent connection changed; use --reset-thread to keep progress with a new session")
                self.conn.execute("UPDATE contest SET agent_command=? WHERE id=1", (encoded_command,))
                self.conn.commit()
            if model is not None or reasoning_effort is not None or cyber_access_program is not None:
                self.conn.execute(
                    "UPDATE contest SET model=COALESCE(?, model), "
                    "reasoning_effort=COALESCE(?, reasoning_effort), "
                    "cyber_access_program=COALESCE(?, cyber_access_program), updated_at=? "
                    "WHERE id=1",
                    (model, reasoning_effort, cyber_access_program, time.time()),
                )
                self.conn.commit()
        return self.contest()

    def contest(self) -> dict[str, object]:
        row = self.conn.execute("SELECT * FROM contest WHERE id=1").fetchone()
        if row is None:
            raise ValueError("no live contest exists in this state directory")
        return dict(row)

    def set_model_settings(self, model: str | None,
                           reasoning_effort: str | None) -> None:
        """Change the saved native settings without resetting any problem threads."""
        if model is not None and (
            not model.strip() or len(model) > 100 or any(ord(char) < 32 for char in model)
        ):
            raise ValueError("invalid Codex model name")
        if reasoning_effort not in {None, "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}:
            raise ValueError("unsupported reasoning effort")
        with self.conn:
            result = self.conn.execute(
                "UPDATE contest SET model=?, reasoning_effort=?, updated_at=? WHERE id=1",
                (model, reasoning_effort, time.time()),
            )
            if result.rowcount != 1:
                raise ValueError("no live contest exists in this state directory")

    def _append_event(self, kind: str, message: str, *, turn_id: int | None = None,
                      challenge_key: str | None = None) -> None:
        """Participate in the caller's transaction; never commit turn state early."""
        if kind not in EVENT_KINDS:
            raise ValueError("invalid live event kind")
        role = "site"
        if turn_id is not None:
            turn = self.conn.execute("SELECT challenge_key, role FROM turns WHERE id=?", (turn_id,)).fetchone()
            if turn is None:
                raise ValueError("event references an unknown turn")
            role = turn["role"]
            if challenge_key is None:
                challenge_key = turn["challenge_key"]
        cur = self.conn.execute(
            "INSERT INTO live_events(created_at, turn_id, challenge_key, role, kind, message) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (time.time(), turn_id, challenge_key, role, kind, public_text(message)),
        )
        self.conn.execute("DELETE FROM live_events WHERE id<=?", (cur.lastrowid - EVENT_LIMIT,))

    def record_event(self, turn_id: int, kind: str, message: str) -> None:
        with self.conn:
            self._append_event(kind, message, turn_id=turn_id)

    def record_token_usage(self, turn_id: int, input_tokens: int,
                           cached_input_tokens: int, output_tokens: int) -> None:
        values = (input_tokens, cached_input_tokens, output_tokens)
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("token usage must contain nonnegative integers")
        if cached_input_tokens > input_tokens:
            raise ValueError("cached input tokens cannot exceed input tokens")
        with self.conn:
            result = self.conn.execute(
                "UPDATE turns SET input_tokens=?, cached_input_tokens=?, output_tokens=? WHERE id=?",
                (*values, turn_id),
            )
            if result.rowcount != 1:
                raise ValueError("token usage references an unknown turn")

    def latest_event_id(self) -> int:
        return int(self.conn.execute("SELECT COALESCE(MAX(id), 0) FROM live_events").fetchone()[0])

    def events(self, *, limit: int = 50, challenge: str | None = None,
               after: int | None = None, through: int | None = None) -> list[dict]:
        if not 0 <= limit <= EVENT_LIMIT:
            raise ValueError(f"event limit must be between 0 and {EVENT_LIMIT}")
        clauses, values = [], []
        if challenge is not None:
            clauses.append("challenge_key=?")
            values.append(challenge)
        if after is not None:
            clauses.append("id>?")
            values.append(after)
        if through is not None:
            clauses.append("id<=?")
            values.append(through)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.conn.execute(
            "SELECT * FROM live_events" + where + " ORDER BY id DESC LIMIT ?", (*values, limit),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def select_engine(self, engine: str | None) -> dict[str, object]:
        if engine is None:
            row = self.contest()
            if row["engine"] != "codex-shift":
                return row
            # Old native threads remain usable; only the retired runner changes.
            engine = "codex"
        if engine != "codex":
            raise ValueError("unsupported live engine")
        with self.conn:
            row = self.contest()
            if row["agent_command"] and (row["thread_id"] or self.conn.execute(
                "SELECT 1 FROM challenge_work WHERE thread_id IS NOT NULL LIMIT 1"
            ).fetchone()):
                raise ValueError("switching from an external connection needs --reset-thread")
            # Keep native thread IDs and all challenge progress during migration.
            self.conn.execute(
                "UPDATE contest SET engine=?, agent_command=NULL, updated_at=? WHERE id=1",
                (engine, time.time()),
            )
        return self.contest()

    def recover_interrupted_turns(self) -> int:
        now = time.time()
        with self.conn:
            cur = self.conn.execute(
                "UPDATE turns SET state='interrupted', finished_at=? WHERE state='running'",
                (now,),
            )
            if cur.rowcount:
                self.conn.execute("UPDATE contest SET state='interrupted', updated_at=? WHERE id=1", (now,))
        return cur.rowcount

    def reset_thread(self) -> None:
        with self.conn:
            self.conn.execute("UPDATE contest SET thread_id=NULL, updated_at=? WHERE id=1", (time.time(),))
            self.conn.execute("UPDATE challenge_work SET thread_id=NULL")

    def require_running_turn(self, turn_id: int) -> dict[str, object]:
        row = self.conn.execute(
            "SELECT t.state, t.challenge_key, t.role, c.state AS challenge_state FROM turns t "
            "LEFT JOIN challenges c ON c.challenge_key=t.challenge_key WHERE t.id=?", (turn_id,)
        ).fetchone()
        if row is None or row["state"] != "running":
            raise ValueError("live turn is not running")
        if row["challenge_key"] is not None and row["challenge_state"] == "solved":
            raise ValueError("challenge is already solved; turn is stopped")
        return dict(row)

    def resume_handoff(self) -> None:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE contest SET state='active', updated_at=? "
                "WHERE id=1 AND state='needs_human'",
                (time.time(),),
            )
            if cur.rowcount != 1:
                raise ValueError("contest is not waiting for a login handoff")

    def begin_turn(self, challenge_key: str | None = None, *, role: str = "legacy") -> int:
        if role not in {"legacy", "discovery", "monitor", "solver"}:
            raise ValueError("invalid live turn role")
        if role == "solver" and challenge_key is None:
            raise ValueError("solver needs a challenge")
        now = time.time()
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            if challenge_key is not None:
                row = self.conn.execute(
                    "SELECT state FROM challenges WHERE challenge_key=?", (challenge_key,)
                ).fetchone()
                if row is None or row["state"] == "solved":
                    raise ValueError("challenge is missing or solved")
                if self.conn.execute(
                    "SELECT 1 FROM turns WHERE challenge_key=? AND state='running' LIMIT 1", (challenge_key,)
                ).fetchone():
                    raise ValueError("challenge already has a running turn")
            cur = self.conn.execute(
                "INSERT INTO turns(state, started_at, challenge_key, role) VALUES('running', ?, ?, ?)",
                (now, challenge_key, role),
            )
            if challenge_key is not None:
                self.conn.execute(
                    "INSERT INTO challenge_work(challenge_key, attempts, last_attempt) VALUES(?, 1, ?) "
                    "ON CONFLICT(challenge_key) DO UPDATE SET attempts=attempts+1, last_attempt=excluded.last_attempt",
                    (challenge_key, now),
                )
                self.workspace(challenge_key).mkdir(parents=True, exist_ok=True, mode=0o700)
            self.conn.execute("UPDATE contest SET state='running', updated_at=? WHERE id=1", (now,))
            self._append_event("turn.started", {
                "discovery": "사이트 로그인·문제 목록 확인", "monitor": "새 문제·사이트 완료 상태 확인",
                "solver": "문제 풀이 시작", "legacy": "작업 시작",
            }[role], turn_id=int(cur.lastrowid))
        return int(cur.lastrowid)

    def set_thread_id(self, thread_id: str) -> None:
        if not thread_id or len(thread_id) > 200:
            raise ValueError("invalid Codex thread id")
        with self.conn:
            cur = self.conn.execute(
                "UPDATE contest SET thread_id=?, updated_at=? "
                "WHERE id=1 AND (thread_id IS NULL OR thread_id=?)",
                (thread_id, time.time(), thread_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError("Codex thread id changed unexpectedly")

    def thread_for(self, challenge_key: str | None) -> str | None:
        if challenge_key is None:
            return self.contest()["thread_id"]
        row = self.conn.execute(
            "SELECT thread_id FROM challenge_work WHERE challenge_key=?", (challenge_key,)
        ).fetchone()
        return str(row["thread_id"]) if row and row["thread_id"] else None

    def set_turn_thread(self, turn_id: int, thread_id: str) -> None:
        if not thread_id or len(thread_id) > 200:
            raise ValueError("invalid agent thread id")
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            turn = self.require_running_turn(turn_id)
            if turn["challenge_key"] is None:
                cur = self.conn.execute(
                    "UPDATE contest SET thread_id=? WHERE id=1 AND (thread_id IS NULL OR thread_id=?)",
                    (thread_id, thread_id),
                )
            else:
                cur = self.conn.execute(
                    "UPDATE challenge_work SET thread_id=? WHERE challenge_key=? "
                    "AND (thread_id IS NULL OR thread_id=?)",
                    (thread_id, turn["challenge_key"], thread_id),
                )
            if cur.rowcount != 1:
                raise RuntimeError("agent thread id changed unexpectedly")

    def browser_session_for(self, challenge_key: str | None) -> str:
        base = str(self.contest()["browser_session"])
        return base if challenge_key is None else base + "-" + hashlib.sha256(
            challenge_key.encode()
        ).hexdigest()[:12]

    def mark_solved(self, challenge_key: str, *, source: str = "user",
                    evidence: str = "") -> bool:
        if source not in {"user", "site", "agent"}:
            raise ValueError("invalid solve source")
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute(
                "SELECT state FROM challenges WHERE challenge_key=?", (challenge_key,)
            ).fetchone()
            if row is None:
                raise ValueError("unknown challenge key")
            if row["state"] == "solved":
                return False
            now = time.time()
            self.conn.execute(
                "UPDATE challenges SET state='solved', solved_source=?, site_evidence=?, updated_at=? "
                "WHERE challenge_key=?",
                (source, evidence[:500], now, challenge_key),
            )
            self._stop_solved(challenge_key, now)
            self._append_event("challenge.solved", f"완료 처리 ({source}) · 해당 작업 중단 요청",
                               challenge_key=challenge_key)
            return True

    def _stop_solved(self, challenge_key: str, now: float, *, except_turn: int | None = None) -> None:
        self.conn.execute(
            "UPDATE submissions SET resolved_by_solve=1, updated_at=? WHERE challenge_key=? "
            "AND verdict IN ('submitting', 'unknown')",
            (now, challenge_key),
        )
        self.conn.execute(
            "UPDATE turns SET state='interrupted', error_kind='challenge_solved', finished_at=? "
            "WHERE challenge_key=? AND state='running' AND id!=?",
            (now, challenge_key, except_turn or -1),
        )

    def fail_turn(self, turn_id: int, error_kind: str) -> None:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE turns SET state='failed', error_kind=?, finished_at=? "
                "WHERE id=? AND state='running'",
                (error_kind[:100], time.time(), turn_id),
            )
            if cur.rowcount:
                self._append_event("turn.failed", error_kind, turn_id=turn_id)
                self.conn.execute(
                    "UPDATE contest SET state='failed', updated_at=? WHERE id=1",
                    (time.time(),),
                )

    def interrupt_turn(self, turn_id: int, reason: str = "controller_stopped") -> None:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE turns SET state='interrupted', error_kind=?, finished_at=? "
                "WHERE id=? AND state='running'",
                (reason[:100], time.time(), turn_id),
            )
            if cur.rowcount:
                self._append_event("turn.interrupted", reason, turn_id=turn_id)

    def defer_challenge(self, challenge_key: str, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("retry delay must be nonnegative")
        with self.conn:
            self.conn.execute(
                "UPDATE challenge_work SET retry_at=MAX(retry_at, ?) WHERE challenge_key=?",
                (time.time() + seconds, challenge_key),
            )

    def begin_submission(self, challenge_key: str, flag_sha256: str,
                         *, turn_id: int | None = None) -> str:
        key = challenge_key.strip()
        digest = flag_sha256.lower()
        if not key or len(key) > 250 or not _SHA256.fullmatch(digest):
            raise ValueError("challenge key and 64-character SHA-256 digest are required")
        now = time.time()
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            if turn_id is not None:
                turn = self.require_running_turn(turn_id)
                if turn["challenge_key"] != key and turn["role"] != "legacy":
                    raise ValueError("turn is not assigned to this challenge")
            existing = self.conn.execute(
                "SELECT verdict FROM submissions WHERE challenge_key=? AND flag_sha256=?",
                (key, digest),
            ).fetchone()
            if existing is not None and existing["verdict"] in {"accepted", "rejected"}:
                return str(existing["verdict"])
            solved = self.conn.execute(
                "SELECT state FROM challenges WHERE challenge_key=?", (key,)
            ).fetchone()
            if solved is not None and solved["state"] == "solved":
                return "solved"
            if existing is not None:
                return str(existing["verdict"])
            pending = self.conn.execute(
                "SELECT 1 FROM submissions WHERE challenge_key=? AND resolved_by_solve=0 "
                "AND verdict IN ('submitting', 'unknown')",
                (key,),
            ).fetchone()
            if pending is not None:
                return "pending"
            self.conn.execute(
                "INSERT OR IGNORE INTO challenges(challenge_key, title, url, state, updated_at) "
                "VALUES(?, ?, '', 'unknown', ?)",
                (key, key, now),
            )
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO submissions(challenge_key, flag_sha256, verdict, created_at, updated_at) "
                "VALUES(?, ?, 'submitting', ?, ?)",
                (key, digest, now, now),
            )
            if cur.rowcount:
                self._append_event("submission.started", "후보 등록 · 사이트 제출 준비",
                                   turn_id=turn_id, challenge_key=key)
                return "new"
            row = self.conn.execute(
                "SELECT verdict FROM submissions WHERE challenge_key=? AND flag_sha256=?",
                (key, digest),
            ).fetchone()
        return str(row["verdict"])

    def snapshot(self, turn_id: int | None = None) -> dict[str, object]:
        contest = self.contest()
        running = list(self.conn.execute(
            "SELECT id, challenge_key, role FROM turns WHERE state='running' ORDER BY id DESC LIMIT 2"
        )) if turn_id is None else []
        active = (self.conn.execute(
            "SELECT id, challenge_key, role FROM turns WHERE id=?", (turn_id,)
        ).fetchone() if turn_id is not None else running[0] if len(running) == 1 else None)
        assigned = None
        if active is not None and active["challenge_key"] is not None:
            row = self.conn.execute("SELECT challenge_key, title, url, state, solved_source "
                                    "FROM challenges WHERE challenge_key=?",
                                    (active["challenge_key"],)).fetchone()
            if row is not None:
                assigned = dict(row)
                assigned["workspace"] = str(self.workspace(str(row["challenge_key"])))
        challenges = [
            dict(row)
            for row in self.conn.execute(
                "SELECT c.challenge_key, c.title, c.url, c.state, c.solved_source, "
                "COALESCE(w.note, '') AS note, "
                "COALESCE(w.next_action, '') AS next_action, COALESCE(w.attempts, 0) AS attempts, "
                "COALESCE(w.retry_at, 0) AS retry_at FROM challenges c "
                "LEFT JOIN challenge_work w ON w.challenge_key=c.challenge_key "
                "ORDER BY c.state='solved', COALESCE(w.last_attempt, 0), c.challenge_key LIMIT 100"
            )
        ]
        pending = [
            dict(row)
            for row in self.conn.execute(
                "SELECT challenge_key, flag_sha256, verdict FROM submissions "
                "WHERE resolved_by_solve=0 AND verdict IN ('submitting', 'unknown') ORDER BY created_at"
            )
        ]
        counts = {
            str(row["verdict"]): int(row["n"])
            for row in self.conn.execute(
                "SELECT verdict, COUNT(*) AS n FROM submissions GROUP BY verdict"
            )
        }
        return {
            "site_url": contest["site_url"],
            "account_label": contest["account_label"],
            "auth_entry": contest["auth_entry"],
            "browser_session": self.browser_session_for(
                str(active["challenge_key"]) if active is not None and active["challenge_key"] else None
            ),
            "thread_id": self.thread_for(
                str(active["challenge_key"]) if active is not None and active["challenge_key"] else None
            ),
            "model": contest["model"],
            "reasoning_effort": contest["reasoning_effort"],
            "cyber_access_program": contest["cyber_access_program"],
            "agent_command": json.loads(contest["agent_command"]) if contest["agent_command"] else None,
            "engine": "external" if contest["agent_command"] else (contest["engine"] or "codex"),
            "last_note": contest["last_note"],
            "state": contest["state"],
            "challenges": challenges,
            "pending_submissions": pending,
            "submission_counts": counts,
            "challenge_count": int(self.conn.execute("SELECT COUNT(*) FROM challenges").fetchone()[0]),
            "next_challenge": self.next_challenge(),
            "turn_id": int(active["id"]) if active else None,
            "turn_role": str(active["role"]) if active else None,
            "assigned_challenge": assigned,
            "running_challenges": [str(row[0]) for row in self.conn.execute(
                "SELECT challenge_key FROM turns WHERE state='running' AND challenge_key IS NOT NULL"
            )],
            "rejected_candidates": [dict(row) for row in self.conn.execute(
                "SELECT challenge_key, flag_sha256 FROM submissions WHERE verdict='rejected' "
                "ORDER BY updated_at DESC LIMIT 200"
            )],
        }

    def workspace(self, key: str) -> Path:
        return self.state_dir / "workspaces" / hashlib.sha256(key.encode()).hexdigest()[:24]

    def next_challenge(self) -> dict[str, object] | None:
        row = self.conn.execute(
            "SELECT c.challenge_key, c.title, c.url, c.state, COALESCE(w.note, '') AS note, "
            "COALESCE(w.next_action, '') AS next_action, COALESCE(w.attempts, 0) AS attempts, "
            "EXISTS(SELECT 1 FROM submissions s WHERE s.challenge_key=c.challenge_key "
            "AND s.resolved_by_solve=0 AND s.verdict IN ('submitting', 'unknown')) AS reconcile "
            "FROM challenges c LEFT JOIN challenge_work w ON w.challenge_key=c.challenge_key "
            "WHERE c.state!='solved' AND NOT EXISTS(SELECT 1 FROM turns t WHERE "
            "t.challenge_key=c.challenge_key AND t.state='running') AND "
            "(COALESCE(w.retry_at, 0)<=? OR "
            "EXISTS(SELECT 1 FROM submissions s WHERE s.challenge_key=c.challenge_key "
            "AND s.resolved_by_solve=0 AND s.verdict IN ('submitting', 'unknown'))) "
            "ORDER BY reconcile DESC, COALESCE(w.last_attempt, 0), c.challenge_key LIMIT 1",
            (time.time(),),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["workspace"] = str(self.workspace(str(row["challenge_key"])))
        return result

    def all_solved(self) -> bool:
        row = self.conn.execute("SELECT COUNT(*) AS total, SUM(state!='solved') AS remaining FROM challenges").fetchone()
        pending = self.conn.execute("SELECT 1 FROM submissions WHERE resolved_by_solve=0 "
                                    "AND verdict IN ('submitting', 'unknown') LIMIT 1").fetchone()
        return bool(row["total"] and not row["remaining"] and pending is None)

    def finish_turn(
        self,
        turn_id: int,
        report: dict[str, object],
        *,
        browser_observations: tuple[str, ...] = (),
    ) -> None:
        outcome = report.get("outcome")
        challenges = report.get("challenges")
        submissions = report.get("submissions")
        work = report.get("work", [])
        note = report.get("note")
        if outcome not in _OUTCOMES or not isinstance(note, str):
            raise ValueError("invalid live-turn outcome or note")
        if not isinstance(challenges, list) or not isinstance(submissions, list) or not isinstance(work, list):
            raise ValueError("live-turn challenges and submissions must be lists")
        now = time.time()
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute(
                "SELECT state, challenge_key, role FROM turns WHERE id=?", (turn_id,)
            ).fetchone()
            if row is None or row["state"] != "running":
                raise RuntimeError("live turn is not running")
            focus = row["challenge_key"]
            if focus and self.conn.execute(
                "SELECT state FROM challenges WHERE challenge_key=?", (focus,)
            ).fetchone()["state"] == "solved":
                raise RuntimeError("challenge was solved while turn was running")
            if row["role"] in {"discovery", "monitor"} and (submissions or work):
                raise ValueError("discovery and monitor turns cannot submit or change challenge work")
            if row["role"] == "solver" and any(
                not isinstance(value, dict) or value.get("challenge_key") != focus
                for value in submissions + work
            ):
                raise ValueError("solver may only report work and submissions for its assigned challenge")
            if row["role"] == "solver" and any(
                not isinstance(value, dict) or value.get("key") != focus
                for value in challenges
            ):
                raise ValueError("solver may only report its assigned challenge")
            newly_solved: set[str] = set()
            previously_solved = {value[0] for value in self.conn.execute(
                "SELECT challenge_key FROM challenges WHERE state='solved'"
            )}
            for value in challenges:
                if not isinstance(value, dict):
                    raise ValueError("invalid challenge report")
                key, title, url, state, evidence = (
                    value.get("key"), value.get("title"), value.get("url"),
                    value.get("state"), value.get("site_evidence"),
                )
                if not all(isinstance(v, str) for v in (key, title, url, state, evidence)):
                    raise ValueError("invalid challenge fields")
                if not key or len(key) > 250 or state not in {"open", "solved", "unknown"}:
                    raise ValueError("invalid challenge key or state")
                if state == "solved" and (
                    not evidence.strip() or
                    not any(evidence in observed for observed in browser_observations)
                ):
                    raise ValueError("solved challenge needs observed site evidence")
                if state == "solved":
                    newly_solved.add(key)
                self.conn.execute(
                    "INSERT INTO challenges(challenge_key, title, url, state, site_evidence, updated_at, solved_source) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?) ON CONFLICT(challenge_key) DO UPDATE SET "
                    "title=excluded.title, url=excluded.url, "
                    "state=CASE WHEN challenges.state='solved' THEN 'solved' ELSE excluded.state END, "
                    "solved_source=COALESCE(challenges.solved_source, excluded.solved_source), "
                    "site_evidence=CASE WHEN excluded.site_evidence='' THEN challenges.site_evidence "
                    "ELSE excluded.site_evidence END, "
                    "updated_at=excluded.updated_at",
                    (key, title[:500], url[:2000], state, evidence[:500], now,
                     ("site" if row["role"] in {"discovery", "monitor"} else "agent")
                     if state == "solved" else None),
                )
            for value in submissions:
                if not isinstance(value, dict):
                    raise ValueError("invalid submission report")
                key, digest, verdict, evidence, url = (
                    value.get("challenge_key"), value.get("flag_sha256"),
                    value.get("verdict"), value.get("site_evidence"),
                    value.get("observed_url"),
                )
                if not all(isinstance(v, str) for v in (key, digest, verdict, evidence, url)):
                    raise ValueError("invalid submission fields")
                if verdict not in _VERDICTS or not _SHA256.fullmatch(digest):
                    raise ValueError("invalid submission verdict or digest")
                if verdict != "unknown" and (not evidence.strip() or not url.strip()):
                    raise ValueError("site evidence and URL are required for a verdict")
                if verdict != "unknown" and not any(
                    evidence in observed for observed in browser_observations
                ):
                    raise ValueError("submission verdict needs observed browser evidence")
                cur = self.conn.execute(
                    "UPDATE submissions SET verdict=?, site_evidence=?, observed_url=?, updated_at=? "
                    "WHERE challenge_key=? AND flag_sha256=? "
                    "AND verdict IN ('submitting', 'unknown', ?)",
                    (verdict, evidence[:500], url[:2000], now, key, digest, verdict),
                )
                if cur.rowcount != 1:
                    raise ValueError("submission was not journaled before browser action")
                self._append_event("submission.verdict", verdict, turn_id=turn_id, challenge_key=key)
                if verdict == "accepted":
                    newly_solved.add(key)
                    self.conn.execute(
                        "UPDATE challenges SET state='solved', "
                        "solved_source=COALESCE(solved_source, 'agent'), updated_at=? "
                        "WHERE challenge_key=?", (now, key),
                    )
            for value in work:
                if not isinstance(value, dict):
                    raise ValueError("invalid challenge work checkpoint")
                key, work_note, next_action, retry = (
                    value.get("challenge_key"), value.get("note"),
                    value.get("next_action"), value.get("retry_after_seconds"),
                )
                if (not all(isinstance(v, str) for v in (key, work_note, next_action))
                    or not isinstance(retry, int) or isinstance(retry, bool) or not 0 <= retry <= 86400):
                    raise ValueError("invalid challenge work checkpoint fields")
                if self.conn.execute("SELECT 1 FROM challenges WHERE challenge_key=?", (key,)).fetchone() is None:
                    raise ValueError("work checkpoint references an unknown challenge")
                self.conn.execute(
                    "INSERT INTO challenge_work(challenge_key, note, next_action, retry_at) VALUES(?, ?, ?, ?) "
                    "ON CONFLICT(challenge_key) DO UPDATE SET note=excluded.note, "
                    "next_action=excluded.next_action, retry_at=excluded.retry_at",
                    (key, work_note[:4000], next_action[:1000], now + retry),
                )
                self._append_event("checkpoint", work_note + " · 다음: " + next_action,
                                   turn_id=turn_id, challenge_key=key)
            for key in newly_solved:
                self._stop_solved(key, now, except_turn=turn_id)
                if key not in previously_solved:
                    self._append_event("challenge.solved", "사이트 정답·완료 확인",
                                       turn_id=turn_id, challenge_key=key)
            self.conn.execute(
                "UPDATE contest SET last_note=?, "
                "state=CASE WHEN state='needs_human' THEN state ELSE ? END, updated_at=? WHERE id=1",
                (note[:1000], outcome, now),
            )
            self.conn.execute(
                "UPDATE turns SET state='finished', outcome=?, finished_at=? WHERE id=?",
                (outcome, now, turn_id),
            )
            self._append_event("turn.finished", str(outcome) + " · " + note, turn_id=turn_id)

    def summary(self) -> dict[str, object]:
        snap = self.snapshot()
        parsed = urlsplit(str(snap["site_url"]))
        latest_turn = self.conn.execute(
            "SELECT state, error_kind FROM turns ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "site_origin": f"{parsed.scheme}://{parsed.netloc}",
            "engine": snap["engine"],
            "model": snap["model"],
            "reasoning_effort": snap["reasoning_effort"],
            "state": snap["state"],
            "thread_id": self.contest()["thread_id"],
            "challenge_count": snap["challenge_count"],
            "solved_count": int(self.conn.execute("SELECT COUNT(*) FROM challenges WHERE state='solved'").fetchone()[0]),
            "all_solved": self.all_solved(),
            "next_challenge": snap["next_challenge"],
            "submission_counts": snap["submission_counts"],
            "pending_submissions": len(snap["pending_submissions"]),
            "last_note": snap["last_note"],
            "last_turn_state": latest_turn["state"] if latest_turn else None,
            "last_error_kind": latest_turn["error_kind"] if latest_turn else None,
            "running_challenges": snap["running_challenges"],
            "solved_by_user": int(self.conn.execute(
                "SELECT COUNT(*) FROM challenges WHERE solved_source='user'"
            ).fetchone()[0]),
        }


class LiveRunLock:
    """OS-released exclusive controller lease; submission helpers stay usable."""

    def __init__(self, state_dir: Path):
        state_dir = state_dir.expanduser().resolve()
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = state_dir / ".runner.lock"
        self.fd: int | None = None

    def __enter__(self) -> LiveRunLock:
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise ValueError("a live contest controller already owns this state directory") from exc
        self.fd = fd
        return self

    def __exit__(self, *_: object) -> None:
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None
