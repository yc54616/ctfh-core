from __future__ import annotations

import fcntl
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean
from typing import Iterable

from .evidence import VerifiedTraceEvidence
from .util import (
    LearningError,
    atomic_json,
    canonical_json,
    copy_snapshot,
    make_tree_read_only,
    require_sha256,
    sha256_bytes,
    tree_snapshot_digest,
)
from .wiki import append_wiki_event, replay_wiki


CANDIDATE_SCHEMA = "ctfh.learning-candidate.v1"
EVALUATION_SCHEMA = "ctfh.learning-evaluation.v1"
DECISION_SCHEMA = "ctfh.learning-decision.v1"
SPLIT_SCHEMA = "ctfh.learning-evaluation-splits.v1"


@dataclass(frozen=True)
class EvaluationRecord:
    task_id: str
    variant: str
    passed: bool
    authoritative: bool
    authoritative_result_s: float
    evaluator_version: str
    runlock_base_hash: str = ""
    candidate_artifact_digest: str | None = None
    split: str = "promotion"

    def validate(self) -> None:
        if not self.task_id:
            raise LearningError("evaluation task_id is required")
        if self.variant not in {"baseline", "candidate"}:
            raise LearningError(
                f"invalid evaluation variant: {self.variant!r}"
            )
        if self.split not in {"promotion", "final"}:
            raise LearningError(f"invalid evaluation split: {self.split!r}")
        if not self.authoritative:
            raise LearningError(
                f"evaluation must be authoritative: {self.task_id}"
            )
        if self.authoritative_result_s < 0:
            raise LearningError(
                f"negative authoritative_result_s: {self.task_id}"
            )
        if not self.evaluator_version:
            raise LearningError(
                f"missing evaluator_version: {self.task_id}"
            )
        require_sha256(
            self.runlock_base_hash,
            f"evaluation runlock_base_hash for {self.task_id}",
        )
        if self.variant == "candidate":
            require_sha256(
                self.candidate_artifact_digest,
                f"candidate_artifact_digest for {self.task_id}",
            )
        elif self.candidate_artifact_digest is not None:
            raise LearningError(
                "baseline evaluation must not claim a candidate artifact digest"
            )


def _load_candidate(path: Path) -> dict[str, object]:
    manifest = path.resolve(strict=True)
    if manifest.stat().st_mode & 0o222:
        raise LearningError("candidate manifest must be sealed read-only")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LearningError(f"invalid candidate manifest: {manifest}") from exc
    if not isinstance(data, dict) or data.get("schema") != CANDIDATE_SCHEMA:
        raise LearningError("unsupported candidate manifest")

    kind = data.get("kind")
    if kind not in {"knowledge", "skill"}:
        raise LearningError("candidate kind must be knowledge or skill")
    digest = require_sha256(
        data.get("content_digest"),
        "content_digest",
    )

    source_task_ids = data.get("source_task_ids")
    if not isinstance(source_task_ids, list) or not source_task_ids:
        raise LearningError("candidate source_task_ids must be a non-empty list")
    if not all(isinstance(value, str) and value for value in source_task_ids):
        raise LearningError("candidate source_task_ids contains invalid values")

    source_trace_hashes = data.get("source_trace_hashes")
    if not isinstance(source_trace_hashes, list) or not source_trace_hashes:
        raise LearningError(
            "candidate source_trace_hashes must be a non-empty list"
        )
    checked_source_hashes = [
        require_sha256(value, "source_trace_hash")
        for value in source_trace_hashes
    ]

    wiki_event_hashes = data.get("wiki_event_hashes")
    if not isinstance(wiki_event_hashes, list):
        raise LearningError("candidate wiki_event_hashes must be a list")
    checked_wiki_hashes = [
        require_sha256(value, "wiki_event_hash")
        for value in wiki_event_hashes
    ]

    parent_digest = data.get("parent_digest")
    if parent_digest is not None:
        parent_digest = require_sha256(parent_digest, "parent_digest")
    note = data.get("note")
    if not isinstance(note, str):
        raise LearningError("candidate note must be a string")

    core: dict[str, object] = {
        "schema": CANDIDATE_SCHEMA,
        "kind": kind,
        "content_digest": digest,
        "source_task_ids": source_task_ids,
        "source_trace_hashes": checked_source_hashes,
        "wiki_event_hashes": checked_wiki_hashes,
        "parent_digest": parent_digest,
        "note": note,
    }
    expected_hash = sha256_bytes(canonical_json(core))
    candidate_hash = require_sha256(
        data.get("candidate_hash"),
        "candidate_hash",
    )
    if candidate_hash != expected_hash:
        raise LearningError("candidate provenance hash mismatch")
    expected_id = f"{kind}-{candidate_hash[:16]}"
    if data.get("candidate_id") != expected_id:
        raise LearningError("candidate_id does not match candidate_hash")

    content_rel = data.get("content_path")
    if not isinstance(content_rel, str) or not content_rel:
        raise LearningError("candidate content_path is required")
    content = (manifest.parent / content_rel).resolve(strict=True)
    try:
        content.relative_to(manifest.parent.resolve())
    except ValueError as exc:
        raise LearningError("candidate content escapes candidate directory") from exc
    if not content.is_dir():
        raise LearningError("candidate content is not a directory")
    if tree_snapshot_digest(content) != digest:
        raise LearningError("candidate content digest mismatch")
    return data


def create_candidate(
    root: Path,
    *,
    kind: str,
    content_dir: Path,
    source_evidence: Iterable[VerifiedTraceEvidence],
    wiki_event_hashes: Iterable[str] = (),
    parent_digest: str | None = None,
    note: str = "",
) -> Path:
    if kind not in {"knowledge", "skill"}:
        raise LearningError("candidate kind must be knowledge or skill")

    evidence = tuple(source_evidence)
    if not evidence:
        raise LearningError("candidate requires at least one source trace")
    source_tasks = sorted({item.task_id for item in evidence})
    source_hashes = sorted({item.manifest_sha256 for item in evidence})
    wiki_hashes = sorted(
        {
            require_sha256(value, "wiki_event_hash")
            for value in wiki_event_hashes
        }
    )
    if parent_digest is not None:
        parent_digest = require_sha256(parent_digest, "parent_digest")

    source = content_dir.resolve()
    if kind == "skill":
        skill_manifests = sorted(source.rglob("SKILL.md"))
        if skill_manifests != [source / "SKILL.md"]:
            raise LearningError(
                "atomic skill candidate must contain exactly one root SKILL.md"
            )
    content_digest = tree_snapshot_digest(source)
    core: dict[str, object] = {
        "schema": CANDIDATE_SCHEMA,
        "kind": kind,
        "content_digest": content_digest,
        "source_task_ids": source_tasks,
        "source_trace_hashes": source_hashes,
        "wiki_event_hashes": wiki_hashes,
        "parent_digest": parent_digest,
        "note": note,
    }
    candidate_hash = sha256_bytes(canonical_json(core))
    candidate_id = f"{kind}-{candidate_hash[:16]}"

    candidate_dir = root.resolve() / "candidates" / candidate_id
    content_out = candidate_dir / "content"
    manifest_path = candidate_dir / "candidate.json"

    if candidate_dir.exists():
        existing = _load_candidate(manifest_path)
        if existing.get("candidate_hash") != candidate_hash:
            raise LearningError("candidate id collision")
        return manifest_path

    candidate_dir.mkdir(parents=True, exist_ok=False)
    copied_digest = copy_snapshot(source, content_out)
    if copied_digest != content_digest:
        raise LearningError("candidate copy digest mismatch")
    make_tree_read_only(content_out)

    record: dict[str, object] = {
        **core,
        "candidate_id": candidate_id,
        "candidate_hash": candidate_hash,
        "content_path": "content",
    }
    atomic_json(manifest_path, record, read_only=True)
    return manifest_path


def _records_by_task(
    records: Iterable[EvaluationRecord],
    *,
    expected_variant: str,
) -> dict[str, EvaluationRecord]:
    result: dict[str, EvaluationRecord] = {}
    for record in records:
        record.validate()
        if record.variant != expected_variant:
            raise LearningError(
                f"expected {expected_variant} record, got {record.variant}"
            )
        if record.split != "promotion":
            raise LearningError(
                "promotion gate only accepts split=promotion evidence"
            )
        if record.task_id in result:
            raise LearningError(
                f"duplicate evaluation task: {record.task_id}"
            )
        result[record.task_id] = record
    if not result:
        raise LearningError("promotion evaluation set is empty")
    return result


def _split_registry_path(root: Path) -> Path:
    return root / "evaluation-splits.json"


def _normalize_task_ids(
    values: Iterable[str],
    *,
    label: str,
) -> list[str]:
    normalized = sorted({str(value) for value in values if str(value)})
    if not normalized:
        raise LearningError(f"{label} task set must not be empty")
    return normalized


def _resolve_evaluation_splits(
    root: Path,
    *,
    promotion_task_ids: Iterable[str],
    requested_final_task_ids: Iterable[str],
    commit_if_missing: bool = True,
) -> tuple[str, ...]:
    promotion = _normalize_task_ids(
        promotion_task_ids,
        label="promotion",
    )
    requested_final = sorted(
        {str(value) for value in requested_final_task_ids if str(value)}
    )
    path = _split_registry_path(root)

    if path.exists():
        if path.stat().st_mode & 0o222:
            raise LearningError("evaluation split registry must be read-only")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LearningError("invalid evaluation split registry") from exc
        if not isinstance(data, dict) or data.get("schema") != SPLIT_SCHEMA:
            raise LearningError("unsupported evaluation split registry")
        stored_promotion = data.get("promotion_task_ids")
        stored_final = data.get("final_task_ids")
        if not isinstance(stored_promotion, list) or not all(
            isinstance(value, str) and value for value in stored_promotion
        ):
            raise LearningError("invalid promotion task registry")
        if not isinstance(stored_final, list) or not all(
            isinstance(value, str) and value for value in stored_final
        ):
            raise LearningError("invalid final task registry")
        if stored_promotion != promotion:
            raise LearningError(
                "promotion task set differs from immutable split registry"
            )
        if requested_final and stored_final != requested_final:
            raise LearningError(
                "final task set differs from immutable split registry"
            )
        return tuple(stored_final)

    if not requested_final:
        raise LearningError(
            "first promotion decision requires a non-empty final untouched task set"
        )
    if set(promotion) & set(requested_final):
        raise LearningError(
            "promotion tasks overlap final untouched evaluation tasks"
        )
    if commit_if_missing:
        payload: dict[str, object] = {
            "schema": SPLIT_SCHEMA,
            "promotion_task_ids": promotion,
            "final_task_ids": requested_final,
        }
        atomic_json(path, payload, read_only=True)
    return tuple(requested_final)


def evaluate_candidate(
    candidate_manifest: Path,
    *,
    baseline_records: Iterable[EvaluationRecord],
    candidate_records: Iterable[EvaluationRecord],
    final_reserved_task_ids: Iterable[str] = (),
    min_solve_rate_delta: float = 0.0,
    max_latency_regression_ratio: float = 0.25,
) -> dict[str, object]:
    if min_solve_rate_delta < -1 or min_solve_rate_delta > 1:
        raise LearningError("min_solve_rate_delta must be in [-1, 1]")
    if max_latency_regression_ratio < 0:
        raise LearningError(
            "max_latency_regression_ratio must be non-negative"
        )

    candidate = _load_candidate(candidate_manifest)
    source_tasks = set(str(v) for v in candidate["source_task_ids"])
    baseline = _records_by_task(
        baseline_records,
        expected_variant="baseline",
    )
    proposed = _records_by_task(
        candidate_records,
        expected_variant="candidate",
    )
    if set(baseline) != set(proposed):
        raise LearningError(
            "baseline and candidate evaluation task sets must match"
        )
    promotion_tasks = set(baseline)
    overlap = source_tasks & promotion_tasks
    if overlap:
        raise LearningError(
            "source-trace tasks overlap promotion evaluation tasks: "
            + ", ".join(sorted(overlap))
        )

    final_reserved = {str(v) for v in final_reserved_task_ids if str(v)}
    if source_tasks & final_reserved:
        raise LearningError(
            "source-trace tasks overlap final untouched evaluation tasks"
        )
    if promotion_tasks & final_reserved:
        raise LearningError(
            "promotion tasks overlap final untouched evaluation tasks"
        )

    evaluator_versions: set[str] = set()
    for task_id in sorted(promotion_tasks):
        left = baseline[task_id]
        right = proposed[task_id]
        if left.evaluator_version != right.evaluator_version:
            raise LearningError(
                f"evaluator version mismatch for task {task_id}"
            )
        if right.candidate_artifact_digest != candidate["content_digest"]:
            raise LearningError(
                f"candidate evaluation is bound to the wrong artifact: {task_id}"
            )
        evaluator_versions.add(left.evaluator_version)

    baseline_rate = mean(
        1.0 if record.passed else 0.0 for record in baseline.values()
    )
    candidate_rate = mean(
        1.0 if record.passed else 0.0 for record in proposed.values()
    )
    baseline_latency = mean(
        record.authoritative_result_s for record in baseline.values()
    )
    candidate_latency = mean(
        record.authoritative_result_s for record in proposed.values()
    )

    solve_delta = candidate_rate - baseline_rate
    if baseline_latency == 0:
        latency_ratio = 1.0 if candidate_latency == 0 else float("inf")
    else:
        latency_ratio = candidate_latency / baseline_latency

    solve_ok = solve_delta >= min_solve_rate_delta
    latency_ok = latency_ratio <= 1.0 + max_latency_regression_ratio
    promote = solve_ok and latency_ok
    reasons: list[str] = []
    if not solve_ok:
        reasons.append(
            f"solve-rate delta {solve_delta:.6f} below "
            f"required {min_solve_rate_delta:.6f}"
        )
    if not latency_ok:
        reasons.append(
            f"latency ratio {latency_ratio:.6f} exceeds "
            f"limit {1.0 + max_latency_regression_ratio:.6f}"
        )
    if promote:
        reasons.append("promotion thresholds satisfied")

    return {
        "schema": EVALUATION_SCHEMA,
        "candidate_id": candidate["candidate_id"],
        "candidate_hash": candidate["candidate_hash"],
        "content_digest": candidate["content_digest"],
        "source_task_ids": sorted(source_tasks),
        "promotion_task_ids": sorted(promotion_tasks),
        "final_reserved_task_ids": sorted(final_reserved),
        "evaluator_versions": sorted(evaluator_versions),
        "thresholds": {
            "min_solve_rate_delta": min_solve_rate_delta,
            "max_latency_regression_ratio": max_latency_regression_ratio,
        },
        "baseline": {
            "solve_rate": baseline_rate,
            "mean_authoritative_result_s": baseline_latency,
            "records": [
                asdict(baseline[key]) for key in sorted(baseline)
            ],
        },
        "candidate": {
            "solve_rate": candidate_rate,
            "mean_authoritative_result_s": candidate_latency,
            "records": [
                asdict(proposed[key]) for key in sorted(proposed)
            ],
        },
        "decision": "promote" if promote else "reject",
        "reasons": reasons,
    }


def _ensure_decision_wiki_event(
    wiki_root: Path,
    *,
    candidate: dict[str, object],
    evaluation: dict[str, object],
    evaluation_hash: str,
    decision: str,
) -> None:
    for event in replay_wiki(wiki_root):
        if event.get("kind") not in {"promotion", "rejection"}:
            continue
        body = event.get("body")
        if not isinstance(body, dict):
            continue
        if (
            body.get("candidate_id") == candidate["candidate_id"]
            and body.get("evaluation_hash") == evaluation_hash
            and body.get("decision") == decision
        ):
            return

    append_wiki_event(
        wiki_root,
        kind="promotion" if decision == "promote" else "rejection",
        body={
            "candidate_id": candidate["candidate_id"],
            "content_digest": candidate["content_digest"],
            "evaluation_hash": evaluation_hash,
            "decision": decision,
            "reasons": evaluation["reasons"],
            "thresholds": evaluation["thresholds"],
            "baseline": {
                "solve_rate": evaluation["baseline"]["solve_rate"],
                "mean_authoritative_result_s": (
                    evaluation["baseline"]["mean_authoritative_result_s"]
                ),
            },
            "candidate": {
                "solve_rate": evaluation["candidate"]["solve_rate"],
                "mean_authoritative_result_s": (
                    evaluation["candidate"]["mean_authoritative_result_s"]
                ),
            },
        },
        task_ids=[
            *candidate["source_task_ids"],
            *evaluation["promotion_task_ids"],
        ],
        source_trace_hashes=candidate["source_trace_hashes"],
        tags=("candidate-decision", str(candidate["kind"])),
    )


def promote_or_reject(
    root: Path,
    candidate_manifest: Path,
    *,
    baseline_records: Iterable[EvaluationRecord],
    candidate_records: Iterable[EvaluationRecord],
    final_reserved_task_ids: Iterable[str] = (),
    min_solve_rate_delta: float = 0.0,
    max_latency_regression_ratio: float = 0.25,
    wiki_root: Path | None = None,
) -> dict[str, object]:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    candidate = _load_candidate(candidate_manifest)
    baseline_records = tuple(baseline_records)
    candidate_records = tuple(candidate_records)
    promotion_task_ids = sorted(
        {record.task_id for record in baseline_records if record.task_id}
    )
    if not promotion_task_ids:
        raise LearningError("promotion evaluation set is empty")
    requested_final = tuple(
        str(value) for value in final_reserved_task_ids if str(value)
    )

    lock_path = root / ".promotion.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)

        decision_path = (
            root
            / "decisions"
            / f"{candidate['candidate_id']}.json"
        )
        if decision_path.exists():
            if decision_path.stat().st_mode & 0o222:
                raise LearningError("terminal decision must be read-only")
            try:
                decision_record = json.loads(
                    decision_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as exc:
                raise LearningError("invalid terminal decision") from exc
            if not isinstance(decision_record, dict):
                raise LearningError("terminal decision must be an object")
            if decision_record.get("schema") != DECISION_SCHEMA:
                raise LearningError("unsupported terminal decision schema")
            if decision_record.get("candidate_hash") != candidate["candidate_hash"]:
                raise LearningError(
                    "terminal decision candidate hash does not match manifest"
                )
            stored_promotion = decision_record.get("promotion_task_ids")
            if stored_promotion != promotion_task_ids:
                raise LearningError(
                    "promotion task set differs from terminal candidate decision"
                )
            effective_final = _resolve_evaluation_splits(
                root,
                promotion_task_ids=promotion_task_ids,
                requested_final_task_ids=requested_final,
            )
            if decision_record.get("final_reserved_task_ids") != list(
                effective_final
            ):
                raise LearningError(
                    "terminal decision final task set does not match split registry"
                )

            evaluation_path = Path(
                str(decision_record.get("evaluation_path", ""))
            )
            if not evaluation_path.is_file():
                raise LearningError("terminal decision evaluation is missing")
            if evaluation_path.stat().st_mode & 0o222:
                raise LearningError("terminal evaluation must be read-only")
            try:
                evaluation = json.loads(
                    evaluation_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as exc:
                raise LearningError("invalid terminal evaluation") from exc
            evaluation_hash = require_sha256(
                decision_record.get("evaluation_hash"),
                "evaluation_hash",
            )
            if sha256_bytes(canonical_json(evaluation)) != evaluation_hash:
                raise LearningError("terminal evaluation hash mismatch")

            if decision_record.get("decision") == "promote":
                promoted_path = Path(
                    str(decision_record.get("promoted_path", ""))
                )
                if not promoted_path.is_dir():
                    raise LearningError("promoted content is missing")
                if (
                    tree_snapshot_digest(promoted_path)
                    != candidate["content_digest"]
                ):
                    raise LearningError("promoted content digest mismatch")

            if wiki_root is not None:
                _ensure_decision_wiki_event(
                    wiki_root,
                    candidate=candidate,
                    evaluation=evaluation,
                    evaluation_hash=evaluation_hash,
                    decision=str(decision_record["decision"]),
                )

            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            return {
                "evaluation": evaluation,
                "evaluation_hash": evaluation_hash,
                "evaluation_path": str(evaluation_path),
                "decision": decision_record,
                "decision_path": str(decision_path),
            }

        effective_final = _resolve_evaluation_splits(
            root,
            promotion_task_ids=promotion_task_ids,
            requested_final_task_ids=requested_final,
            commit_if_missing=False,
        )
        evaluation = evaluate_candidate(
            candidate_manifest,
            baseline_records=baseline_records,
            candidate_records=candidate_records,
            final_reserved_task_ids=effective_final,
            min_solve_rate_delta=min_solve_rate_delta,
            max_latency_regression_ratio=max_latency_regression_ratio,
        )
        committed_final = _resolve_evaluation_splits(
            root,
            promotion_task_ids=promotion_task_ids,
            requested_final_task_ids=effective_final,
            commit_if_missing=True,
        )
        if tuple(evaluation["final_reserved_task_ids"]) != committed_final:
            raise LearningError(
                "evaluation final task set does not match split registry"
            )
        evaluation_hash = sha256_bytes(canonical_json(evaluation))
        evaluation_path = (
            root
            / "evaluations"
            / f"{candidate['candidate_id']}-{evaluation_hash[:12]}.json"
        )
        if not evaluation_path.exists():
            atomic_json(evaluation_path, evaluation, read_only=True)

        decision = str(evaluation["decision"])
        promoted_path: str | None = None
        if decision == "promote":
            content = candidate_manifest.resolve().parent / str(
                candidate["content_path"]
            )
            destination = (
                root
                / "promoted"
                / str(candidate["kind"])
                / str(candidate["content_digest"])
            )
            if destination.exists():
                if (
                    tree_snapshot_digest(destination)
                    != candidate["content_digest"]
                ):
                    raise LearningError(
                        "existing promoted content digest mismatch"
                    )
            else:
                copied = copy_snapshot(content, destination)
                if copied != candidate["content_digest"]:
                    raise LearningError("promoted content digest mismatch")
                make_tree_read_only(destination)
            promoted_path = str(destination)

        decision_record: dict[str, object] = {
            "schema": DECISION_SCHEMA,
            "candidate_id": candidate["candidate_id"],
            "candidate_hash": candidate["candidate_hash"],
            "kind": candidate["kind"],
            "content_digest": candidate["content_digest"],
            "evaluation_hash": evaluation_hash,
            "evaluation_path": str(evaluation_path),
            "decision": decision,
            "promoted_path": promoted_path,
            "source_task_ids": candidate["source_task_ids"],
            "source_trace_hashes": candidate["source_trace_hashes"],
            "promotion_task_ids": evaluation["promotion_task_ids"],
            "final_reserved_task_ids": evaluation[
                "final_reserved_task_ids"
            ],
        }
        atomic_json(decision_path, decision_record, read_only=True)

        if wiki_root is not None:
            _ensure_decision_wiki_event(
                wiki_root,
                candidate=candidate,
                evaluation=evaluation,
                evaluation_hash=evaluation_hash,
                decision=decision,
            )

        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return {
            "evaluation": evaluation,
            "evaluation_hash": evaluation_hash,
            "evaluation_path": str(evaluation_path),
            "decision": decision_record,
            "decision_path": str(decision_path),
        }
