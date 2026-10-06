from __future__ import annotations

import asyncio
import fcntl
import json
import os
import shutil
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Iterator

from .agent import AgentAdapter, make_agent_adapter
from .artifacts import ArtifactStore
from .environment import CancelToken, LocalWorkerEnvironmentProvider
from .outcome import LocalOutcomeAuthority
from .runlock import apply_knowledge_prompt, build_runlock, resolve_knowledge_snapshot, write_runlock
from .store import StateStore
from .target import LocalTargetProvider
from .trace import seal_trace_manifest, trace_manifest_reference
from .types import (
    AttemptRequest,
    AttemptState,
    RunMetrics,
    TaskSpec,
    Verdict,
    WorkerOwnerTag,
)


DEFAULT_PROMPT = """Harmless local benchmark task only.
Work only inside the current workspace. Do not access the network or any external
site. Do not inspect parent directories. Read TASK.md and the public clue files.
Follow TASK.md exactly and create candidate.txt with the requested single-line
answer. Stop after candidate.txt is correct.
"""


@contextmanager
def host_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open("w")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        f.close()


def next_host_epoch(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        current = int(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        current = 0
    epoch = current + 1
    temp = path.with_suffix(".tmp")
    with temp.open("w") as f:
        f.write(str(epoch))
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)
    return epoch


async def _run_agent_with_deadline(
    adapter: AgentAdapter,
    req: AttemptRequest,
    env,
    env_provider: LocalWorkerEnvironmentProvider,
    attempt_output: Path,
    cancel: CancelToken,
    deadline_s: float,
    before_cancel: Callable[[], None] | None = None,
):
    task = asyncio.create_task(
        adapter.run(req, env, env_provider, attempt_output, cancel)
    )
    try:
        done, _ = await asyncio.wait({task}, timeout=deadline_s)
        if task in done:
            return await task, False

        if before_cancel is not None:
            before_cancel()
        cancel.cancel()
        await env_provider.kill(env, grace_s=0.5)
        try:
            result = await asyncio.wait_for(task, timeout=3.0)
        except (TimeoutError, asyncio.CancelledError):
            result = None
        return result, True
    except asyncio.CancelledError:
        if before_cancel is not None:
            try:
                before_cancel()
            except Exception:
                pass
        cancel.cancel()
        kill_task = asyncio.create_task(
            env_provider.kill(env, grace_s=0.5)
        )
        try:
            await asyncio.shield(kill_task)
        except asyncio.CancelledError:
            await asyncio.shield(kill_task)
        task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=3.0,
            )
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        raise


async def run_supervised(
    *,
    task: TaskSpec,
    run_root: Path,
    model: str,
    reasoning_effort: str,
    evaluator_data: dict[str, object],
    sandbox: str = "danger-full-access",
    prompt: str = DEFAULT_PROMPT,
    host_state_dir: Path | None = None,
    interactive_session_service: bool = False,
    browser_service: bool = False,
    browser_allowed_domains: tuple[str, ...] = (),
    knowledge_snapshot_dir: Path | None = None,
    agent: str = "codex",
) -> dict[str, object]:
    run_root = run_root.resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    e2e_start = time.monotonic()
    queue_start = e2e_start

    adapter = make_agent_adapter(agent)
    knowledge_source, memory_mode, knowledge_digest = resolve_knowledge_snapshot(
        knowledge_snapshot_dir
    )
    effective_prompt = apply_knowledge_prompt(prompt, memory_mode)
    target_provider = LocalTargetProvider()
    env_provider = LocalWorkerEnvironmentProvider(
        run_root / "environments",
        enable_interactive_session_service=interactive_session_service,
        enable_browser_service=browser_service,
        browser_allowed_domains=browser_allowed_domains,
        knowledge_snapshot_dir=knowledge_source,
    )
    outcome = LocalOutcomeAuthority(evaluator_data)
    artifacts = ArtifactStore(run_root / "artifacts")
    cancel = CancelToken()

    run_id = f"run-{uuid.uuid4().hex[:12]}"
    attempt_id = f"attempt-{uuid.uuid4().hex[:12]}"
    attempt_output = run_root / "attempts" / attempt_id
    attempt_output.mkdir(parents=True, exist_ok=True)

    admitted = ["native-shell", "workspace-files"]
    if interactive_session_service:
        admitted.append("interactive-session")
    if browser_service:
        admitted.append("browser")
    admitted_capabilities = tuple(admitted)
    environment_services = {
        "interactive-session": (
            "cli-unix-rpc-v1" if interactive_session_service else "disabled"
        ),
        "browser": (
            "ghostchrome-serve-cli-v1" if browser_service else "disabled"
        ),
    }
    lock = build_runlock(
        treatment="ctfh-supervised",
        task=task,
        adapter=adapter,
        model=model,
        reasoning_effort=reasoning_effort,
        sandbox=sandbox,
        deadline_seconds=task.deadline_seconds,
        prompt=effective_prompt,
        admitted_capabilities=admitted_capabilities,
        memory_mode=memory_mode,
        knowledge_snapshot_digest=knowledge_digest,
        environment_services=environment_services,
        browser_allowed_domains=browser_allowed_domains,
    )
    write_runlock(run_root / "runlock.json", lock)

    host_state_dir = (
        Path.home() / ".cache" / "ctfh-core"
        if host_state_dir is None
        else host_state_dir.resolve()
    )
    owner: WorkerOwnerTag | None = None
    env_created = False
    with host_lock(host_state_dir / "host.lock"):
        store = StateStore(run_root / "state.db")
        try:
            epoch = next_host_epoch(host_state_dir / "epoch")
            recovered = store.recover_live_attempts()
            await env_provider.sweep_workers(set())

            store.ensure_task(task.task_id)
            target_epoch = store.task_target_epoch(task.task_id)
            target = await target_provider.acquire(
                run_id=run_id,
                task_id=task.task_id,
                epoch=target_epoch,
            )
            target_state = await target_provider.reconcile(
                target,
                authoritative_epoch=target_epoch,
            )
            if target_state != "usable":
                raise RuntimeError("target reconciliation failed before attempt admission")

            owner = WorkerOwnerTag(run_id, epoch, task.task_id, attempt_id)
            store.create_attempt(attempt_id, task.task_id, owner.label())
            store.transition_attempt(attempt_id, AttemptState.STARTING)
            store.event("attempt_starting", attempt_id=attempt_id, owner=owner.label())

            env_start = time.monotonic()
            env = await env_provider.create(task, owner, cancel)
            env_created = True
            adapter.materialize(env, env_provider, lock.base_config)
            env_ready = time.monotonic()
            store.transition_attempt(attempt_id, AttemptState.RUNNING)
            store.event("attempt_running", attempt_id=attempt_id)

            req = AttemptRequest(
                task=task,
                target=target,
                owner=owner,
                model=model,
                reasoning_effort=reasoning_effort,
                sandbox=sandbox,
                prompt=effective_prompt,
            )

            queue_wait = env_start - queue_start
            result, timed_out = await _run_agent_with_deadline(
                adapter,
                req,
                env,
                env_provider,
                attempt_output,
                cancel,
                task.deadline_seconds,
                before_cancel=lambda: store.transition_attempt(
                    attempt_id, AttemptState.CANCELLING
                ),
            )

            if timed_out:
                verdict = Verdict(
                    passed=False,
                    authoritative=True,
                    reason="attempt deadline exceeded",
                    candidate=None if result is None else result.candidate,
                )
            else:
                assert result is not None
                store.transition_attempt(
                    attempt_id,
                    AttemptState.FINISHED,
                    exit_code=result.exit_code,
                )
                current_epoch = store.task_target_epoch(task.task_id)
                target_state = await target_provider.reconcile(
                    target,
                    authoritative_epoch=current_epoch,
                )
                if target_state != "usable" or current_epoch != target.epoch:
                    verdict = Verdict(
                        passed=False,
                        authoritative=True,
                        reason="stale target epoch",
                        candidate=result.candidate,
                    )
                else:
                    verify_start = time.monotonic()
                    verdict = await outcome.verify(task, target, env, result.candidate)
                    verify_end = time.monotonic()
                    if verdict.passed:
                        committed = store.commit_solved(
                            task.task_id,
                            target_epoch=target.epoch,
                            outcome={
                                "candidate": verdict.candidate,
                                "attempt_id": attempt_id,
                                "reason": verdict.reason,
                            },
                        )
                        if not committed:
                            verdict = Verdict(
                                passed=False,
                                authoritative=True,
                                reason="authoritative outcome lost CAS race",
                                candidate=verdict.candidate,
                            )

            authoritative_result_s = (
                time.monotonic() - e2e_start if verdict.authoritative else None
            )

            # Preserve useful attempt outputs before destroying the environment.
            candidate_copy = attempt_output / "candidate.txt"
            final_copy = attempt_output / "final_message.txt"
            if result is not None:
                source_candidate = env.workspace / "candidate.txt"
                if source_candidate.is_file():
                    shutil.copy2(source_candidate, candidate_copy)
                final_copy.write_text(result.final_message)
                if result.trace_path.is_file():
                    artifacts.publish_file(result.trace_path)
                if candidate_copy.is_file():
                    artifacts.publish_file(candidate_copy)
                artifacts.publish_file(final_copy)

            cleanup_start = time.monotonic()
            await env_provider.kill(env)
            service_traces = await env_provider.publish_service_traces(
                env, attempt_output
            )
            trace_manifest_path, trace_manifest_payload = seal_trace_manifest(
                attempt_output,
                task_id=task.task_id,
                attempt_id=attempt_id,
                runlock_base_hash=lock.base_hash,
                adapter_name=adapter.name,
                agent_trace_coverage=adapter.capabilities().trace_coverage,
                timed_out=timed_out,
            )
            trace_manifest = trace_manifest_reference(
                trace_manifest_path, trace_manifest_payload
            )
            for service_trace in service_traces:
                artifacts.publish_file(service_trace)
            trace_manifest_artifact = artifacts.publish_file(
                trace_manifest_path
            )
            attempt_artifact = artifacts.seal_directory(attempt_output)
            await env_provider.destroy_cancellation_safe(owner)
            env_created = False
            if timed_out:
                await env_provider.sweep_workers(set())
                store.transition_attempt(attempt_id, AttemptState.CANCELLED)
            cleanup_end = time.monotonic()

            if result is None:
                agent_duration = task.deadline_seconds
                first_seen = None
                verify_duration = 0.0
                exit_code = None
            else:
                agent_duration = result.duration_s
                first_seen = result.candidate_first_seen_s
                verify_duration = (
                    verify_end - verify_start
                    if "verify_start" in locals()
                    else 0.0
                )
                exit_code = result.exit_code

            metrics = RunMetrics(
                queue_wait_s=queue_wait,
                environment_prepare_s=env_ready - env_start,
                agent_duration_s=agent_duration,
                candidate_first_seen_s=first_seen,
                verification_s=verify_duration,
                authoritative_result_s=authoritative_result_s,
                cleanup_s=cleanup_end - cleanup_start,
                end_to_end_s=cleanup_end - e2e_start,
            )
            payload = {
                "mode": "ctfh-supervised",
                "run_id": run_id,
                "kernel_epoch": epoch,
                "recovered_attempts": recovered,
                "task_id": task.task_id,
                "target_epoch": target.epoch,
                "attempt_id": attempt_id,
                "runlock_base_hash": lock.base_hash,
                "runlock_full_hash": lock.full_hash,
                "verdict": asdict(verdict),
                "exit_code": exit_code,
                "trace_manifest": {
                    **trace_manifest,
                    "artifact": trace_manifest_artifact,
                },
                "attempt_artifact": attempt_artifact,
                "metrics": asdict(metrics),
            }
            (run_root / "result.json").write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n"
            )
            return payload
        finally:
            try:
                if owner is not None and env_created:
                    cancel.cancel()
                    await env_provider.destroy_cancellation_safe(owner)
            finally:
                store.close()
