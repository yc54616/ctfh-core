from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .evidence import verify_trace_manifest
from .promotion import EvaluationRecord, create_candidate, promote_or_reject
from .util import LearningError
from .wiki import append_wiki_event, replay_wiki


_EVAL_KEYS = {
    "task_id",
    "passed",
    "authoritative",
    "authoritative_result_s",
    "evaluator_version",
    "runlock_base_hash",
    "runlock_path",
    "candidate_artifact_digest",
    "split",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ctfh-learn",
        description="Offline CTFH learning/wiki/promotion utilities",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser("verify-trace")
    verify.add_argument("manifest", nargs="+")

    wiki = sub.add_parser("wiki-add")
    wiki.add_argument("--root", required=True)
    wiki.add_argument(
        "--kind",
        required=True,
        choices=(
            "observation",
            "hypothesis",
            "evaluation",
            "promotion",
            "rejection",
            "note",
        ),
    )
    wiki.add_argument("--body", required=True)
    wiki.add_argument("--trace", action="append", default=[])
    wiki.add_argument("--tag", action="append", default=[])

    show = sub.add_parser("wiki-show")
    show.add_argument("--root", required=True)

    propose = sub.add_parser("propose")
    propose.add_argument("--root", required=True)
    propose.add_argument("--kind", required=True, choices=("knowledge", "skill"))
    propose.add_argument("--content", required=True)
    propose.add_argument("--trace", action="append", required=True)
    propose.add_argument("--wiki-event", action="append", default=[])
    propose.add_argument("--parent-digest")
    propose.add_argument("--note", default="")

    decide = sub.add_parser("decide")
    decide.add_argument("--root", required=True)
    decide.add_argument("--candidate", required=True)
    decide.add_argument(
        "--evidence",
        required=True,
        help="safe summary JSON with baseline[] and candidate[] records",
    )
    decide.add_argument("--final-task", action="append", default=[])
    decide.add_argument("--min-solve-rate-delta", type=float, default=0.0)
    decide.add_argument(
        "--max-latency-regression-ratio",
        type=float,
        default=0.25,
    )
    decide.add_argument("--wiki-root")

    return parser


def _record(raw: object, variant: str) -> EvaluationRecord:
    if not isinstance(raw, dict):
        raise LearningError("evaluation record must be an object")
    unknown = set(raw) - _EVAL_KEYS
    if unknown:
        raise LearningError(
            "evaluation record contains forbidden/unknown fields: "
            + ", ".join(sorted(unknown))
        )
    required = {
        "task_id",
        "passed",
        "authoritative",
        "authoritative_result_s",
        "evaluator_version",
        "runlock_base_hash",
        "runlock_path",
    }
    missing = required - set(raw)
    if missing:
        raise LearningError(
            "evaluation record missing fields: "
            + ", ".join(sorted(missing))
        )
    if not isinstance(raw["passed"], bool):
        raise LearningError("evaluation passed must be boolean")
    if not isinstance(raw["authoritative"], bool):
        raise LearningError("evaluation authoritative must be boolean")
    return EvaluationRecord(
        task_id=str(raw["task_id"]),
        variant=variant,
        passed=raw["passed"],
        authoritative=raw["authoritative"],
        authoritative_result_s=float(raw["authoritative_result_s"]),
        evaluator_version=str(raw["evaluator_version"]),
        runlock_base_hash=str(raw["runlock_base_hash"]),
        runlock_path=str(raw["runlock_path"]),
        candidate_artifact_digest=(
            None
            if raw.get("candidate_artifact_digest") is None
            else str(raw["candidate_artifact_digest"])
        ),
        split=str(raw.get("split", "promotion")),
    )


def _load_evaluation(path: Path) -> tuple[
    tuple[EvaluationRecord, ...],
    tuple[EvaluationRecord, ...],
]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LearningError(f"invalid evaluation evidence: {path}") from exc
    if not isinstance(data, dict):
        raise LearningError("evaluation evidence must be a JSON object")
    if set(data) != {"baseline", "candidate"}:
        raise LearningError(
            "evaluation evidence top-level keys must be baseline and candidate"
        )
    if not isinstance(data["baseline"], list) or not isinstance(
        data["candidate"], list
    ):
        raise LearningError("baseline/candidate must be arrays")
    baseline = tuple(_record(raw, "baseline") for raw in data["baseline"])
    candidate = tuple(_record(raw, "candidate") for raw in data["candidate"])
    return baseline, candidate


def _main(args: argparse.Namespace) -> object:
    if args.command == "verify-trace":
        evidence = [
            verify_trace_manifest(Path(value))
            for value in args.manifest
        ]
        return [
            {
                "manifest": str(item.manifest_path),
                "manifest_sha256": item.manifest_sha256,
                "task_id": item.task_id,
                "attempt_id": item.attempt_id,
                "runlock_base_hash": item.runlock_base_hash,
                "producer_adapter": item.producer_adapter,
                "stream_count": len(item.streams),
            }
            for item in evidence
        ]

    if args.command == "wiki-add":
        evidence = tuple(
            verify_trace_manifest(Path(value))
            for value in args.trace
        )
        return append_wiki_event(
            Path(args.root),
            kind=args.kind,
            body=args.body,
            evidence=evidence,
            tags=args.tag,
        )

    if args.command == "wiki-show":
        return list(replay_wiki(Path(args.root)))

    if args.command == "propose":
        evidence = tuple(
            verify_trace_manifest(Path(value))
            for value in args.trace
        )
        path = create_candidate(
            Path(args.root),
            kind=args.kind,
            content_dir=Path(args.content),
            source_evidence=evidence,
            wiki_event_hashes=args.wiki_event,
            parent_digest=args.parent_digest,
            note=args.note,
        )
        return {"candidate_manifest": str(path)}

    if args.command == "decide":
        baseline, candidate = _load_evaluation(Path(args.evidence))
        return promote_or_reject(
            Path(args.root),
            Path(args.candidate),
            baseline_records=baseline,
            candidate_records=candidate,
            final_reserved_task_ids=args.final_task,
            min_solve_rate_delta=args.min_solve_rate_delta,
            max_latency_regression_ratio=args.max_latency_regression_ratio,
            wiki_root=(
                None if args.wiki_root is None else Path(args.wiki_root)
            ),
        )

    raise LearningError(f"unsupported command: {args.command}")


def main() -> int:
    args = _parser().parse_args()
    try:
        result = _main(args)
    except LearningError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
