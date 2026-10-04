from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class TaskState(StrEnum):
    OPEN = "open"
    SOLVED = "solved"
    CLOSED = "closed"


class AttemptState(StrEnum):
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    FINISHED = "finished"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    LOST = "lost"


class OperationState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RECONCILING = "reconciling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class WorkerOwnerTag:
    run_id: str
    kernel_epoch: int
    task_id: str
    attempt_id: str

    def label(self) -> str:
        return f"{self.run_id}:{self.kernel_epoch}:{self.task_id}:{self.attempt_id}"


@dataclass(frozen=True)
class TargetOwnerTag:
    run_id: str
    task_id: str
    target_instance_id: str
    target_epoch: int


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    title: str
    public_dir: Path
    deadline_seconds: float = 900.0
    points: int = 1


@dataclass(frozen=True)
class TaskBundle:
    spec: TaskSpec
    evaluator_data: dict[str, Any]


@dataclass(frozen=True)
class TargetHandle:
    instance_id: str
    epoch: int
    endpoint: str | None
    owner: TargetOwnerTag


@dataclass
class WorkerEnvironment:
    owner: WorkerOwnerTag
    root: Path
    workspace: Path
    processes: set[int] = field(default_factory=set)


@dataclass(frozen=True)
class AgentCapabilities:
    usage_visibility: str = "none"
    budget_control: str = "none"
    rate_control: str = "launch_only"
    trace_coverage: str = "agent-events"


@dataclass(frozen=True)
class AttemptRequest:
    task: TaskSpec
    target: TargetHandle
    owner: WorkerOwnerTag
    model: str
    reasoning_effort: str
    sandbox: str
    prompt: str


@dataclass(frozen=True)
class AttemptResult:
    exit_code: int
    final_message: str
    candidate: str | None
    candidate_first_seen_s: float | None
    started_at_monotonic: float
    finished_at_monotonic: float
    trace_path: Path
    final_message_path: Path

    @property
    def duration_s(self) -> float:
        return self.finished_at_monotonic - self.started_at_monotonic


@dataclass(frozen=True)
class Verdict:
    passed: bool
    authoritative: bool
    reason: str
    candidate: str | None


@dataclass(frozen=True)
class RunLock:
    treatment: str
    base_config: dict[str, Any]
    base_hash: str
    full_hash: str


@dataclass(frozen=True)
class RunMetrics:
    queue_wait_s: float
    environment_prepare_s: float
    agent_duration_s: float
    candidate_first_seen_s: float | None
    verification_s: float
    authoritative_result_s: float | None
    cleanup_s: float
    end_to_end_s: float
