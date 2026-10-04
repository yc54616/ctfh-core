from __future__ import annotations

import asyncio
import json
import shutil
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from .agent import CodexCLIAdapter
from .environment import CancelToken, LocalWorkerEnvironmentProvider
from .kernel import DEFAULT_PROMPT, _run_agent_with_deadline, run_supervised
from .outcome import LocalOutcomeAuthority
from .runlock import build_runlock, write_runlock
from .target import LocalTargetProvider
from .types import AttemptRequest, RunMetrics, TaskSpec, WorkerOwnerTag


async def run_direct(
    *,
    task: TaskSpec,
    run_root: Path,
    model: str,
    reasoning_effort: str,
    evaluator_data: dict[str, object],
    sandbox: str = "workspace-write",
    prompt: str = DEFAULT_PROMPT,
) -> dict[str, object]:
    """Matched direct-native arm.

    Uses the same prepared local environment and exact Codex command builder as
    the supervised arm, but has no durable kernel/store/host-lock lifecycle.
    """
    run_root = run_root.resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    e2e_start = time.monotonic()

    adapter = CodexCLIAdapter()
    target_provider = LocalTargetProvider()
    env_provider = LocalWorkerEnvironmentProvider(run_root / "environments")
    outcome = LocalOutcomeAuthority(evaluator_data)
    cancel = CancelToken()

    run_id = f"direct-{uuid.uuid4().hex[:12]}"
    attempt_id = f"attempt-{uuid.uuid4().hex[:12]}"
    owner = WorkerOwnerTag(run_id, 0, task.task_id, attempt_id)
    attempt_output = run_root / "attempts" / attempt_id
    attempt_output.mkdir(parents=True, exist_ok=True)

    lock = build_runlock(
        treatment="direct-native",
        task=task,
        adapter=adapter,
        model=model,
        reasoning_effort=reasoning_effort,
        sandbox=sandbox,
        deadline_seconds=task.deadline_seconds,
        prompt=prompt,
    )
    write_runlock(run_root / "runlock.json", lock)

    target = await target_provider.acquire(run_id=run_id, task_id=task.task_id)
    env_start = time.monotonic()
    env = await env_provider.create(task, owner, cancel)
    env_ready = time.monotonic()

    req = AttemptRequest(
        task=task,
        target=target,
        owner=owner,
        model=model,
        reasoning_effort=reasoning_effort,
        sandbox=sandbox,
        prompt=prompt,
    )
    result, timed_out = await _run_agent_with_deadline(
        adapter,
        req,
        env,
        env_provider,
        attempt_output,
        cancel,
        task.deadline_seconds,
    )

    verify_start = time.monotonic()
    verdict = await outcome.verify(
        task,
        target,
        env,
        None if result is None else result.candidate,
    )
    if timed_out:
        verdict = type(verdict)(
            passed=False,
            authoritative=True,
            reason="attempt deadline exceeded",
            candidate=None if result is None else result.candidate,
        )
    verify_end = time.monotonic()

    if result is not None:
        source_candidate = env.workspace / "candidate.txt"
        if source_candidate.is_file():
            shutil.copy2(source_candidate, attempt_output / "candidate.txt")
        (attempt_output / "final_message.txt").write_text(result.final_message)

    cleanup_start = time.monotonic()
    await env_provider.destroy(owner)
    cleanup_end = time.monotonic()

    metrics = RunMetrics(
        queue_wait_s=0.0,
        environment_prepare_s=env_ready - env_start,
        agent_duration_s=task.deadline_seconds if result is None else result.duration_s,
        candidate_first_seen_s=None if result is None else result.candidate_first_seen_s,
        verification_s=verify_end - verify_start,
        cleanup_s=cleanup_end - cleanup_start,
        end_to_end_s=cleanup_end - e2e_start,
    )
    payload = {
        "mode": "direct-native",
        "run_id": run_id,
        "task_id": task.task_id,
        "target_epoch": target.epoch,
        "attempt_id": attempt_id,
        "runlock_base_hash": lock.base_hash,
        "runlock_full_hash": lock.full_hash,
        "verdict": asdict(verdict),
        "exit_code": None if result is None else result.exit_code,
        "metrics": asdict(metrics),
    }
    (run_root / "result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )
    return payload


async def run_matched_pair(
    *,
    task: TaskSpec,
    root: Path,
    model: str,
    reasoning_effort: str,
    evaluator_data: dict[str, object],
    sandbox: str = "workspace-write",
) -> dict[str, object]:
    direct = await run_direct(
        task=task,
        run_root=root / "direct",
        model=model,
        reasoning_effort=reasoning_effort,
        evaluator_data=evaluator_data,
        sandbox=sandbox,
    )
    supervised = await run_supervised(
        task=task,
        run_root=root / "supervised",
        model=model,
        reasoning_effort=reasoning_effort,
        evaluator_data=evaluator_data,
        sandbox=sandbox,
    )
    if direct["runlock_base_hash"] != supervised["runlock_base_hash"]:
        raise RuntimeError(
            "matched A/B invalid: non-treatment RunLock fields differ"
        )

    d = direct["metrics"]
    s = supervised["metrics"]
    comparison = {
        "matched": True,
        "base_hash": direct["runlock_base_hash"],
        "direct": direct,
        "supervised": supervised,
        "observed_end_to_end_delta_s": float(s["end_to_end_s"]) - float(d["end_to_end_s"]),
        "comparison_note": "single matched pair; delta includes native-agent run variance and is not a causal wrapper-overhead estimate",
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "comparison.json").write_text(
        json.dumps(comparison, indent=2, sort_keys=True) + "\n"
    )
    return comparison
