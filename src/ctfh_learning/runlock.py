from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .util import LearningError, require_sha256, sha256_bytes


def _stable_hash(value: object) -> str:
    # Must match ctfh_core.runlock._stable_hash exactly.
    return sha256_bytes(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )


@dataclass(frozen=True)
class VerifiedRunLock:
    path: Path
    treatment: str
    base_config: dict[str, object]
    base_hash: str
    full_hash: str


def verify_runlock(path: Path) -> VerifiedRunLock:
    source = path.resolve(strict=True)
    if not source.is_file():
        raise LearningError(f"runlock is not a file: {source}")
    if source.stat().st_mode & 0o222:
        raise LearningError("runlock evidence must be sealed read-only")

    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LearningError(f"invalid runlock: {source}") from exc
    if not isinstance(data, dict):
        raise LearningError("runlock must be a JSON object")
    if set(data) != {
        "treatment",
        "base_config",
        "base_hash",
        "full_hash",
    }:
        raise LearningError("runlock has unexpected top-level fields")

    treatment = data.get("treatment")
    base = data.get("base_config")
    if not isinstance(treatment, str) or not treatment:
        raise LearningError("runlock treatment is required")
    if not isinstance(base, dict):
        raise LearningError("runlock base_config must be an object")

    base_hash = require_sha256(data.get("base_hash"), "runlock base_hash")
    full_hash = require_sha256(data.get("full_hash"), "runlock full_hash")
    if _stable_hash(base) != base_hash:
        raise LearningError("runlock base_hash mismatch")
    if _stable_hash({"treatment": treatment, "base": base}) != full_hash:
        raise LearningError("runlock full_hash mismatch")

    return VerifiedRunLock(
        path=source,
        treatment=treatment,
        base_config=dict(base),
        base_hash=base_hash,
        full_hash=full_hash,
    )


def normalized_profile(
    base_config: dict[str, object],
    *,
    candidate_kind: str,
) -> dict[str, object]:
    """Remove only the candidate treatment from a verified RunLock profile."""
    cloned = json.loads(json.dumps(base_config))
    if candidate_kind == "knowledge":
        cloned.pop("memory", None)
        cloned.pop("prompt_sha256", None)
        cloned.pop("prompt_policy", None)
    elif candidate_kind == "skill":
        cloned.pop("skills", None)
    else:
        raise LearningError(
            f"unsupported candidate kind: {candidate_kind}"
        )
    return cloned


def assert_baseline_excludes_candidate(
    lock: VerifiedRunLock,
    *,
    candidate_kind: str,
    candidate_digest: str,
) -> None:
    digest = require_sha256(candidate_digest, "candidate digest")
    base = lock.base_config

    if candidate_kind == "knowledge":
        memory = base.get("memory")
        if not isinstance(memory, dict):
            raise LearningError("knowledge baseline RunLock missing memory")
        if (
            memory.get("mode") != "off"
            or memory.get("knowledge_snapshot_digest") is not None
            or memory.get("native_memory_isolated") is not True
            or memory.get("read_only") is not True
            or memory.get("materializer") != "none"
        ):
            raise LearningError("knowledge baseline must use isolated memory=off")
        if base.get("prompt_policy") != "none":
            raise LearningError("knowledge baseline prompt policy must be none")
        prompt_hash = require_sha256(
            base.get("prompt_sha256"),
            "baseline prompt_sha256",
        )
        base_prompt_hash = require_sha256(
            base.get("prompt_base_sha256"),
            "baseline prompt_base_sha256",
        )
        if prompt_hash != base_prompt_hash:
            raise LearningError("knowledge baseline prompt is not the base prompt")
        return

    if candidate_kind == "skill":
        skills = base.get("skills")
        if not isinstance(skills, dict):
            raise LearningError("skill baseline RunLock missing skills")
        if skills.get("digest") == digest:
            raise LearningError("skill baseline already contains the candidate")
        return

    raise LearningError(f"unsupported candidate kind: {candidate_kind}")


def assert_candidate_is_pinned(
    lock: VerifiedRunLock,
    *,
    candidate_kind: str,
    candidate_digest: str,
) -> None:
    digest = require_sha256(candidate_digest, "candidate digest")
    base = lock.base_config

    if candidate_kind == "knowledge":
        memory = base.get("memory")
        if not isinstance(memory, dict):
            raise LearningError("candidate RunLock memory is missing")
        if memory.get("mode") != "frozen":
            raise LearningError("candidate RunLock memory is not frozen")
        if memory.get("knowledge_snapshot_digest") != digest:
            raise LearningError(
                "candidate RunLock uses the wrong knowledge digest"
            )
        if memory.get("native_memory_isolated") is not True:
            raise LearningError("candidate RunLock native memory is not isolated")
        if memory.get("read_only") is not True:
            raise LearningError("candidate RunLock knowledge is not read-only")
        if memory.get("materializer") != "snapshot-copy-v1":
            raise LearningError(
                "candidate RunLock uses the wrong knowledge materializer"
            )
        if base.get("prompt_policy") != "frozen-knowledge-guidance-v1":
            raise LearningError("candidate knowledge prompt policy is invalid")
        prompt_hash = require_sha256(
            base.get("prompt_sha256"),
            "candidate prompt_sha256",
        )
        base_prompt_hash = require_sha256(
            base.get("prompt_base_sha256"),
            "candidate prompt_base_sha256",
        )
        if prompt_hash == base_prompt_hash:
            raise LearningError("candidate knowledge effective prompt did not change")
        return

    if candidate_kind == "skill":
        skills = base.get("skills")
        if not isinstance(skills, dict):
            raise LearningError("candidate RunLock skills are missing")
        if (
            skills.get("mode") == "off"
            or skills.get("digest") != digest
            or skills.get("read_only") is not True
            or skills.get("materializer") in {None, "none"}
        ):
            raise LearningError("candidate skill is not pinned by the RunLock")
        return

    raise LearningError(f"unsupported candidate kind: {candidate_kind}")
