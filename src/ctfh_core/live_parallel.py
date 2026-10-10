"""Concurrent challenge workers with an independent site discovery/solve monitor."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

from .intent_gate import brief
from .live_agent import CodexExecRunner, CodexRunError, build_live_prompt
from .live_bridge import JsonLineAgentRunner, TurnRunner
from .live_browser import clear_receipts, load_receipts, verify_verdicts
from .live_artifacts import verify_accepted_artifacts
from .live_store import LiveRunLock, LiveStore
from .live_events import capture_events


async def run_parallel_contest(
    *, state_dir: Path, site_url: str | None, account_label: str | None,
    auth_entry: str | None, model: str | None, cyber_access_program: str | None,
    codex_binary: str, max_turns: int | None, idle_seconds: float,
    turn_timeout: float, resume_handoff: bool = False,
    agent_command: tuple[str, ...] | None = None, reset_thread: bool = False,
    stop_when_solved: bool = False, runner: TurnRunner | None = None,
    max_workers: int | None = None, scan_seconds: float = 15,
    engine: str | None = None,
    quiet: bool = False,
    full_access: bool = False,
    reasoning_effort: str | None = None,
) -> dict[str, object]:
    if max_turns is not None and max_turns < 1:
        raise ValueError("max_turns must be positive")
    if max_workers is not None and max_workers < 1:
        raise ValueError("max-workers must be positive")
    if idle_seconds < 0 or scan_seconds <= 0 or turn_timeout <= 0:
        raise ValueError("idle/scan/turn intervals must be nonnegative/positive")
    if engine is not None and agent_command is not None:
        raise ValueError("choose either --engine or --agent-command")
    repo_root = Path(__file__).resolve().parents[2]
    with LiveRunLock(state_dir), LiveStore(state_dir) as store:
        contest = store.ensure_contest(
            site_url, account_label, auth_entry, model=model,
            reasoning_effort=reasoning_effort,
            cyber_access_program=cyber_access_program,
            agent_command=None if reset_thread else agent_command,
        )
        if reset_thread:
            store.reset_thread()
            contest = store.ensure_contest(None, None, None, agent_command=agent_command)
        contest = store.select_engine(engine)
        store.recover_interrupted_turns()
        clear_receipts(store.state_dir)
        if resume_handoff:
            store.resume_handoff()
        if store.contest()["state"] == "needs_human":
            return store.summary()

        saved_command = tuple(json.loads(str(contest["agent_command"]))) if contest["agent_command"] else None
        intent_brief = brief(repo_root)
        jobs: dict[int, tuple[asyncio.Task[dict[str, object]], str | None, str]] = {}
        started = 0
        next_scan = 0.0
        first_handoff = resume_handoff

        def capacity() -> bool:
            return max_turns is None or started < max_turns

        def worker_capacity() -> bool:
            return max_workers is None or sum(
                1 for task, _, role in jobs.values() if role == "solver" and not task.done()
            ) < max_workers

        async def execute(turn_id: int, key: str | None, role: str,
                          handoff: bool) -> dict[str, object]:
            snapshot = store.snapshot(turn_id)
            prompt = build_live_prompt(
                snapshot, state_dir=store.state_dir, repo_root=repo_root,
                resume_handoff=handoff, intent_brief=intent_brief,
                turn_id=turn_id,
            )
            runner_dir: Path | None = None
            if runner is not None:
                connection = runner
            elif saved_command is not None:
                connection = JsonLineAgentRunner(
                    saved_command, repo_root, store.state_dir, turn_timeout, turn_id=turn_id,
                )
            else:
                # Every Codex process gets private report files and schema.
                runner_dir = store.state_dir / "runners" / str(turn_id)
                runner_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                current_contest = store.contest()
                connection = CodexExecRunner(
                    repo_root, runner_dir, binary=codex_binary,
                    full_access=full_access or role == "solver",
                    model=(current_contest["model"]
                           if isinstance(current_contest["model"], str) else None),
                    reasoning_effort=(current_contest["reasoning_effort"]
                                      if isinstance(current_contest["reasoning_effort"], str) else None),
                    cyber_access_program=(
                        current_contest["cyber_access_program"]
                        if isinstance(current_contest["cyber_access_program"], str) else None
                    ),
                    timeout_seconds=turn_timeout,
                )
            try:
                with capture_events(
                    lambda kind, message: store.record_event(turn_id, kind, message),
                    usage_sink=lambda input_count, cached_count, output_count:
                        store.record_token_usage(turn_id, input_count, cached_count, output_count),
                ):
                    turn = await connection.run(
                        prompt, store.thread_for(key),
                        lambda thread_id: store.set_turn_thread(turn_id, thread_id),
                    )
                report = dict(turn.report)
                if role in {"discovery", "monitor"} and report.get("work"):
                    # Discovery may explain site navigation in note, but only
                    # assigned solver turns own per-challenge work checkpoints.
                    report["work"] = []
                    store.record_event(turn_id, "agent.progress", "발견/감시 턴의 문제별 메모를 제외함")
                receipts = load_receipts(store.state_dir, turn_id)
                verify_verdicts(store, report, receipts)
                verify_accepted_artifacts(store, report)
                store.finish_turn(
                    turn_id, report,
                    browser_observations=tuple(receipt.text for receipt in receipts),
                )
                return report
            except asyncio.CancelledError:
                store.interrupt_turn(turn_id)
                store.record_event(turn_id, "worker.stopped", "작업 중단 확인")
                raise
            except (CodexRunError, ValueError, RuntimeError, OSError) as exc:
                store.fail_turn(
                    turn_id, exc.kind if isinstance(exc, CodexRunError) else type(exc).__name__,
                )
                raise
            finally:
                clear_receipts(store.state_dir, turn_id)
                if runner_dir is not None:
                    shutil.rmtree(runner_dir)

        def launch(role: str, key: str | None = None) -> None:
            nonlocal started, first_handoff
            turn_id = store.begin_turn(key, role=role)
            job = asyncio.create_task(
                execute(turn_id, key, role, first_handoff), name=f"ctfh-{role}-{turn_id}"
            )
            jobs[turn_id] = (job, key, role)
            started += 1
            first_handoff = False

        try:
            while True:
                if store.contest()["state"] == "needs_human":
                    break
                if stop_when_solved and store.all_solved():
                    break

                # A user command or monitor report may solve a problem while its
                # subprocess is still running. Cancel only that process group.
                for task, key, role in jobs.values():
                    if role == "solver" and key and not task.done():
                        row = store.conn.execute(
                            "SELECT state FROM challenges WHERE challenge_key=?", (key,)
                        ).fetchone()
                        if row is not None and row["state"] == "solved" and not task.cancelling():
                            task.cancel()

                scanning = any(role in {"discovery", "monitor"} for _, _, role in jobs.values())
                if capacity() and not scanning and time.monotonic() >= next_scan:
                    launch("discovery" if store.summary()["challenge_count"] == 0 else "monitor")
                    next_scan = time.monotonic() + scan_seconds

                while capacity() and worker_capacity():
                    focus = store.next_challenge()
                    if focus is None:
                        break
                    launch("solver", str(focus["challenge_key"]))

                if not jobs:
                    if not capacity():
                        break
                    await asyncio.sleep(0.25)
                    continue

                done, _ = await asyncio.wait(
                    [entry[0] for entry in jobs.values()],
                    timeout=0.25, return_when=asyncio.FIRST_COMPLETED,
                )
                for turn_id, (task, key, role) in list(jobs.items()):
                    if task not in done:
                        continue
                    del jobs[turn_id]
                    try:
                        report = await task
                    except asyncio.CancelledError:
                        continue
                    except (CodexRunError, ValueError, RuntimeError, OSError) as exc:
                        # A failed discovery/monitor or unavailable account stops
                        # the controller. A single problem failure is retried later.
                        kind = exc.kind if isinstance(exc, CodexRunError) else type(exc).__name__
                        if role != "solver" or kind in {
                            "auth", "quota", "cyber_safety", "launch_failed",
                            "interrupt_failed", "approval_required", "input_required",
                            "connection_failed", "rate_limit",
                            "configuration_failed",
                            "rpc_failed",
                        }:
                            raise
                        if key is not None:
                            row = store.conn.execute(
                                "SELECT state FROM challenges WHERE challenge_key=?", (key,)
                            ).fetchone()
                            if row is not None and row["state"] == "solved":
                                continue
                            store.defer_challenge(key, max(idle_seconds, 1))
                        if not quiet:
                            print(json.dumps({"turn": turn_id, "challenge": key,
                                              "error_kind": kind}, ensure_ascii=False), flush=True)
                        continue
                    if role == "solver" and key is not None and report["outcome"] == "idle":
                        store.defer_challenge(key, idle_seconds)
                    if not quiet:
                        print(json.dumps({"turn": turn_id, "role": role, "challenge": key,
                                          **store.summary()}, ensure_ascii=False), flush=True)
                    if report["outcome"] == "needs_human":
                        break
        finally:
            for turn_id, (task, _, _) in jobs.items():
                if not task.done() and not task.cancelling():
                    store.interrupt_turn(turn_id)
                    task.cancel()
            if jobs:
                await asyncio.gather(*(entry[0] for entry in jobs.values()), return_exceptions=True)
        return store.summary()
