"""Continuous contest controller using the user's native Codex environment."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .live_agent import CodexExecRunner, CodexRunError, build_live_prompt
from .live_store import LiveRunLock, LiveStore
from .intent_gate import brief
from .live_bridge import JsonLineAgentRunner, TurnRunner
from .live_browser import clear_receipts, load_receipts, verify_verdicts
from .live_events import capture_events


async def run_live_contest(
    *,
    state_dir: Path,
    site_url: str | None,
    account_label: str | None,
    auth_entry: str | None,
    model: str | None,
    cyber_access_program: str | None,
    codex_binary: str,
    max_turns: int | None,
    idle_seconds: float,
    turn_timeout: float,
    resume_handoff: bool = False,
    agent_command: tuple[str, ...] | None = None,
    reset_thread: bool = False,
    stop_when_solved: bool = False,
    runner: TurnRunner | None = None,
    parallel: bool = True,
    max_workers: int | None = None,
    scan_seconds: float = 15,
    engine: str | None = None,
    quiet: bool = False,
    full_access: bool = False,
    reasoning_effort: str | None = None,
) -> dict[str, object]:
    if parallel:
        from .live_parallel import run_parallel_contest
        return await run_parallel_contest(
            state_dir=state_dir, site_url=site_url, account_label=account_label,
            auth_entry=auth_entry, model=model,
            cyber_access_program=cyber_access_program, codex_binary=codex_binary,
            max_turns=max_turns, idle_seconds=idle_seconds,
            turn_timeout=turn_timeout, resume_handoff=resume_handoff,
            agent_command=agent_command, reset_thread=reset_thread,
            stop_when_solved=stop_when_solved, runner=runner,
            max_workers=max_workers, scan_seconds=scan_seconds, engine=engine,
            quiet=quiet, full_access=full_access,
            reasoning_effort=reasoning_effort,
        )
    if engine is not None:
        raise ValueError("engine selection requires the parallel controller")
    if max_turns is not None and max_turns < 1:
        raise ValueError("max_turns must be positive")
    if idle_seconds < 0 or turn_timeout <= 0:
        raise ValueError("idle_seconds and turn_timeout must be nonnegative/positive")
    repo_root = Path(__file__).resolve().parents[2]
    with LiveRunLock(state_dir):
        with LiveStore(state_dir) as store:
            contest = store.ensure_contest(
                site_url, account_label, auth_entry,
                model=model, reasoning_effort=reasoning_effort,
                cyber_access_program=cyber_access_program,
                agent_command=None if reset_thread else agent_command,
            )
            contest = store.select_engine(None)
            if reset_thread:
                store.reset_thread()
                contest = store.ensure_contest(None, None, None, agent_command=agent_command)
            store.recover_interrupted_turns()
            clear_receipts(store.state_dir)
            if resume_handoff:
                store.resume_handoff()
            handoff_to_resume = resume_handoff
            if runner is None and contest["agent_command"]:
                runner = JsonLineAgentRunner(
                    tuple(json.loads(str(contest["agent_command"]))), repo_root,
                    store.state_dir, turn_timeout,
                )
            if runner is None:
                runner = CodexExecRunner(
                    repo_root,
                    store.state_dir,
                    binary=codex_binary,
                    full_access=full_access,
                    model=contest["model"] if isinstance(contest["model"], str) else None,
                    reasoning_effort=(contest["reasoning_effort"]
                                      if isinstance(contest["reasoning_effort"], str) else None),
                    cyber_access_program=(
                        contest["cyber_access_program"]
                        if isinstance(contest["cyber_access_program"], str) else None
                    ),
                    timeout_seconds=turn_timeout,
                )
            turns = 0
            while max_turns is None or turns < max_turns:
                snapshot = store.snapshot()
                if snapshot["state"] == "needs_human":
                    break
                if stop_when_solved and store.all_solved():
                    break
                focus = snapshot["next_challenge"]
                turn_id = store.begin_turn(str(focus["challenge_key"]) if focus else None)
                try:
                    prompt = build_live_prompt(
                        snapshot, state_dir=store.state_dir, repo_root=repo_root,
                        resume_handoff=handoff_to_resume,
                        intent_brief=brief(repo_root), turn_id=turn_id,
                    )
                    handoff_to_resume = False
                    with capture_events(
                        lambda kind, message: store.record_event(turn_id, kind, message),
                        usage_sink=lambda input_count, cached_count, output_count:
                            store.record_token_usage(turn_id, input_count, cached_count, output_count),
                    ):
                        turn = await runner.run(
                            prompt,
                            snapshot["thread_id"] if isinstance(snapshot["thread_id"], str) else None,
                            store.set_thread_id,
                        )
                    receipts = load_receipts(store.state_dir, turn_id)
                    verify_verdicts(store, turn.report, receipts)
                    store.finish_turn(
                        turn_id, turn.report,
                        browser_observations=tuple(receipt.text for receipt in receipts),
                    )
                except asyncio.CancelledError:
                    store.fail_turn(turn_id, "cancelled")
                    raise
                except (CodexRunError, ValueError, RuntimeError, OSError) as exc:
                    store.fail_turn(
                        turn_id,
                        exc.kind if isinstance(exc, CodexRunError) else type(exc).__name__,
                    )
                    raise
                finally:
                    clear_receipts(store.state_dir, turn_id)
                turns += 1
                summary = store.summary()
                if not quiet:
                    print(json.dumps({"turn": turns, **summary}, ensure_ascii=False), flush=True)
                if turn.report["outcome"] == "needs_human" or (stop_when_solved and store.all_solved()):
                    break
                if turn.report["outcome"] == "idle" and (max_turns is None or turns < max_turns):
                    await asyncio.sleep(idle_seconds)
            return store.summary()
