from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctfh_core.trace import seal_trace_manifest
from ctfh_learning import (
    EvaluationRecord,
    LearningError,
    append_wiki_event,
    create_candidate,
    promote_or_reject,
    replay_wiki,
    verify_trace_manifest,
)
from ctfh_learning.util import tree_snapshot_digest


RUNLOCK_HASH = "d" * 64


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _sealed_evidence(self, task_id: str = "source-task"):
        attempt = self.root / f"attempt-{task_id}"
        attempt.mkdir(parents=True)
        (attempt / "trace.jsonl").write_text(
            '{"type":"turn.started"}\n{"type":"turn.completed"}\n'
        )
        manifest, _ = seal_trace_manifest(
            attempt,
            task_id=task_id,
            attempt_id=f"attempt-{task_id}",
            runlock_base_hash="a" * 64,
            adapter_name="codex-cli",
            agent_trace_coverage="codex-json-events",
            timed_out=False,
        )
        return verify_trace_manifest(manifest)

    def _candidate(self, evidence, *, text="KNOWLEDGE-V1\n"):
        content = self.root / (
            "candidate-content-"
            + evidence.task_id
            + "-"
            + str(len(list(self.root.glob("candidate-content-*"))))
        )
        content.mkdir()
        (content / "knowledge.md").write_text(text)
        hypothesis = append_wiki_event(
            self.root,
            kind="hypothesis",
            body={"claim": "candidate knowledge may improve held-out tasks"},
            evidence=(evidence,),
            tags=("knowledge", "hypothesis"),
        )
        return create_candidate(
            self.root,
            kind="knowledge",
            content_dir=content,
            source_evidence=(evidence,),
            wiki_event_hashes=(str(hypothesis["event_hash"]),),
            note="atomic knowledge candidate",
        )

    @staticmethod
    def _candidate_digest(candidate: Path) -> str:
        return str(json.loads(candidate.read_text())["content_digest"])

    def _record(
        self,
        *,
        task_id: str,
        variant: str,
        passed: bool,
        latency: float,
        candidate: Path | None = None,
        authoritative: bool = True,
        evaluator_version: str = "eval-v1",
        artifact_digest: str | None = None,
    ) -> EvaluationRecord:
        if variant == "candidate":
            digest = (
                artifact_digest
                if artifact_digest is not None
                else self._candidate_digest(candidate)  # type: ignore[arg-type]
            )
        else:
            digest = None
        return EvaluationRecord(
            task_id=task_id,
            variant=variant,
            passed=passed,
            authoritative=authoritative,
            authoritative_result_s=latency,
            evaluator_version=evaluator_version,
            runlock_base_hash=RUNLOCK_HASH,
            candidate_artifact_digest=digest,
        )

    def test_verify_trace_manifest_rejects_tampering(self):
        evidence = self._sealed_evidence()
        self.assertEqual(evidence.task_id, "source-task")
        self.assertEqual(evidence.producer_adapter, "codex-cli")
        self.assertEqual(len(evidence.streams), 1)

        trace = evidence.manifest_path.parent / "trace.jsonl"
        trace.chmod(0o644)
        trace.write_text('{"type":"tampered"}\n')
        trace.chmod(0o444)
        with self.assertRaisesRegex(LearningError, "digest mismatch"):
            verify_trace_manifest(evidence.manifest_path)

    def test_candidate_provenance_hash_is_rechecked(self):
        evidence = self._sealed_evidence()
        candidate = self._candidate(evidence)
        data = json.loads(candidate.read_text())
        data["note"] = "tampered provenance"
        candidate.chmod(0o644)
        candidate.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        candidate.chmod(0o444)

        baseline = (
            self._record(
                task_id="heldout-1",
                variant="baseline",
                passed=True,
                latency=1.0,
            ),
        )
        proposed = (
            self._record(
                task_id="heldout-1",
                variant="candidate",
                passed=True,
                latency=1.0,
                candidate=candidate,
            ),
        )
        with self.assertRaisesRegex(
            LearningError,
            "candidate provenance hash mismatch",
        ):
            promote_or_reject(
                self.root,
                candidate,
                baseline_records=baseline,
                candidate_records=proposed,
                final_reserved_task_ids=("final-1",),
            )

    def test_rejected_candidate_does_not_erase_learning_wiki(self):
        evidence = self._sealed_evidence()
        candidate = self._candidate(evidence)

        baseline = (
            self._record(
                task_id="heldout-1",
                variant="baseline",
                passed=True,
                latency=1.0,
            ),
        )
        proposed = (
            self._record(
                task_id="heldout-1",
                variant="candidate",
                passed=True,
                latency=2.0,
                candidate=candidate,
            ),
        )
        result = promote_or_reject(
            self.root,
            candidate,
            baseline_records=baseline,
            candidate_records=proposed,
            final_reserved_task_ids=("final-untouched",),
            max_latency_regression_ratio=0.1,
            wiki_root=self.root,
        )
        self.assertEqual(result["decision"]["decision"], "reject")
        self.assertIsNone(result["decision"]["promoted_path"])

        wiki = replay_wiki(self.root)
        self.assertEqual(
            [event["kind"] for event in wiki],
            ["hypothesis", "rejection"],
        )
        self.assertEqual(
            wiki[0]["body"]["claim"],
            "candidate knowledge may improve held-out tasks",
        )

    def test_terminal_candidate_decision_cannot_flip(self):
        evidence = self._sealed_evidence()
        candidate = self._candidate(evidence)
        baseline = (
            self._record(
                task_id="heldout-1",
                variant="baseline",
                passed=True,
                latency=1.0,
            ),
        )
        slow = (
            self._record(
                task_id="heldout-1",
                variant="candidate",
                passed=True,
                latency=3.0,
                candidate=candidate,
            ),
        )
        first = promote_or_reject(
            self.root,
            candidate,
            baseline_records=baseline,
            candidate_records=slow,
            final_reserved_task_ids=("final-1",),
            max_latency_regression_ratio=0.1,
            wiki_root=self.root,
        )
        self.assertEqual(first["decision"]["decision"], "reject")

        fast = (
            self._record(
                task_id="heldout-1",
                variant="candidate",
                passed=True,
                latency=0.5,
                candidate=candidate,
            ),
        )
        second = promote_or_reject(
            self.root,
            candidate,
            baseline_records=baseline,
            candidate_records=fast,
            max_latency_regression_ratio=10.0,
            wiki_root=self.root,
        )
        self.assertEqual(second["decision"]["decision"], "reject")
        self.assertEqual(first["decision_path"], second["decision_path"])
        self.assertEqual(
            [event["kind"] for event in replay_wiki(self.root)],
            ["hypothesis", "rejection"],
        )
        self.assertFalse((self.root / "promotions").exists())

    def test_terminal_decision_retry_repairs_missing_wiki_event(self):
        evidence = self._sealed_evidence()
        candidate = self._candidate(evidence)
        baseline = (
            self._record(
                task_id="heldout-1",
                variant="baseline",
                passed=True,
                latency=1.0,
            ),
        )
        proposed = (
            self._record(
                task_id="heldout-1",
                variant="candidate",
                passed=True,
                latency=3.0,
                candidate=candidate,
            ),
        )

        with patch(
            "ctfh_learning.promotion.append_wiki_event",
            side_effect=RuntimeError("wiki unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "wiki unavailable"):
                promote_or_reject(
                    self.root,
                    candidate,
                    baseline_records=baseline,
                    candidate_records=proposed,
                    final_reserved_task_ids=("final-1",),
                    max_latency_regression_ratio=0.1,
                    wiki_root=self.root,
                )

        decisions = list((self.root / "decisions").glob("*.json"))
        self.assertEqual(len(decisions), 1)
        self.assertEqual(
            [event["kind"] for event in replay_wiki(self.root)],
            ["hypothesis"],
        )

        repaired = promote_or_reject(
            self.root,
            candidate,
            baseline_records=baseline,
            candidate_records=proposed,
            final_reserved_task_ids=("final-1",),
            max_latency_regression_ratio=0.1,
            wiki_root=self.root,
        )
        self.assertEqual(repaired["decision"]["decision"], "reject")
        self.assertEqual(
            [event["kind"] for event in replay_wiki(self.root)],
            ["hypothesis", "rejection"],
        )

        again = promote_or_reject(
            self.root,
            candidate,
            baseline_records=baseline,
            candidate_records=proposed,
            final_reserved_task_ids=("final-1",),
            max_latency_regression_ratio=0.1,
            wiki_root=self.root,
        )
        self.assertEqual(again["decision_path"], repaired["decision_path"])
        self.assertEqual(
            [event["kind"] for event in replay_wiki(self.root)],
            ["hypothesis", "rejection"],
        )

    def test_promotion_gate_enforces_task_disjointness(self):
        evidence = self._sealed_evidence(task_id="source-task")
        candidate = self._candidate(evidence)

        same_source_baseline = (
            self._record(
                task_id="source-task",
                variant="baseline",
                passed=True,
                latency=1.0,
            ),
        )
        same_source_candidate = (
            self._record(
                task_id="source-task",
                variant="candidate",
                passed=True,
                latency=1.0,
                candidate=candidate,
            ),
        )
        with self.assertRaisesRegex(
            LearningError,
            "source-trace tasks overlap promotion",
        ):
            promote_or_reject(
                self.root,
                candidate,
                baseline_records=same_source_baseline,
                candidate_records=same_source_candidate,
                final_reserved_task_ids=("final-1",),
            )

    def test_split_registry_is_immutable_across_candidates(self):
        evidence = self._sealed_evidence(task_id="source-a")
        first_candidate = self._candidate(evidence, text="FIRST\n")
        first_baseline = (
            self._record(
                task_id="promotion-a",
                variant="baseline",
                passed=True,
                latency=1.0,
            ),
        )
        first_proposed = (
            self._record(
                task_id="promotion-a",
                variant="candidate",
                passed=True,
                latency=2.0,
                candidate=first_candidate,
            ),
        )
        promote_or_reject(
            self.root,
            first_candidate,
            baseline_records=first_baseline,
            candidate_records=first_proposed,
            final_reserved_task_ids=("final-a",),
            max_latency_regression_ratio=0.1,
        )

        second_candidate = self._candidate(evidence, text="SECOND\n")
        second_baseline = (
            self._record(
                task_id="promotion-b",
                variant="baseline",
                passed=True,
                latency=1.0,
            ),
        )
        second_proposed = (
            self._record(
                task_id="promotion-b",
                variant="candidate",
                passed=True,
                latency=1.0,
                candidate=second_candidate,
            ),
        )
        with self.assertRaisesRegex(
            LearningError,
            "promotion task set differs from immutable split registry",
        ):
            promote_or_reject(
                self.root,
                second_candidate,
                baseline_records=second_baseline,
                candidate_records=second_proposed,
                final_reserved_task_ids=("final-a",),
            )

        registry = self.root / "evaluation-splits.json"
        self.assertTrue(registry.is_file())
        self.assertEqual(registry.stat().st_mode & 0o222, 0)

    def test_invalid_first_evaluation_does_not_commit_split_registry(self):
        evidence = self._sealed_evidence(task_id="source-a")
        candidate = self._candidate(evidence)
        baseline = (
            self._record(
                task_id="promotion-a",
                variant="baseline",
                passed=True,
                latency=1.0,
            ),
        )
        proposed = (
            self._record(
                task_id="promotion-a",
                variant="candidate",
                passed=True,
                latency=1.0,
                candidate=candidate,
                artifact_digest="e" * 64,
            ),
        )
        with self.assertRaisesRegex(
            LearningError,
            "bound to the wrong artifact",
        ):
            promote_or_reject(
                self.root,
                candidate,
                baseline_records=baseline,
                candidate_records=proposed,
                final_reserved_task_ids=("final-a",),
            )
        self.assertFalse((self.root / "evaluation-splits.json").exists())

    def test_candidate_evaluation_must_bind_exact_artifact_digest(self):
        evidence = self._sealed_evidence(task_id="source-a")
        candidate = self._candidate(evidence)
        baseline = (
            self._record(
                task_id="promotion-a",
                variant="baseline",
                passed=True,
                latency=1.0,
            ),
        )
        proposed = (
            self._record(
                task_id="promotion-a",
                variant="candidate",
                passed=True,
                latency=1.0,
                candidate=candidate,
                artifact_digest="e" * 64,
            ),
        )
        with self.assertRaisesRegex(
            LearningError,
            "bound to the wrong artifact",
        ):
            promote_or_reject(
                self.root,
                candidate,
                baseline_records=baseline,
                candidate_records=proposed,
                final_reserved_task_ids=("final-a",),
            )

    def test_promotion_creates_versioned_read_only_snapshot(self):
        evidence = self._sealed_evidence(task_id="source-a")
        candidate = self._candidate(
            evidence,
            text="PROMOTED-KNOWLEDGE\n",
        )

        baseline = (
            self._record(
                task_id="promotion-a",
                variant="baseline",
                passed=False,
                latency=2.0,
                evaluator_version="eval-v2",
            ),
            self._record(
                task_id="promotion-b",
                variant="baseline",
                passed=True,
                latency=2.0,
                evaluator_version="eval-v2",
            ),
        )
        proposed = (
            self._record(
                task_id="promotion-a",
                variant="candidate",
                passed=True,
                latency=1.2,
                candidate=candidate,
                evaluator_version="eval-v2",
            ),
            self._record(
                task_id="promotion-b",
                variant="candidate",
                passed=True,
                latency=1.0,
                candidate=candidate,
                evaluator_version="eval-v2",
            ),
        )
        result = promote_or_reject(
            self.root,
            candidate,
            baseline_records=baseline,
            candidate_records=proposed,
            final_reserved_task_ids=("final-a", "final-b"),
            min_solve_rate_delta=0.0,
            max_latency_regression_ratio=0.25,
            wiki_root=self.root,
        )
        decision = result["decision"]
        self.assertEqual(decision["decision"], "promote")
        promoted = Path(str(decision["promoted_path"]))
        self.assertTrue(promoted.is_dir())
        self.assertEqual(
            tree_snapshot_digest(promoted),
            decision["content_digest"],
        )
        self.assertEqual(
            (promoted / "knowledge.md").read_text(),
            "PROMOTED-KNOWLEDGE\n",
        )
        self.assertEqual(promoted.stat().st_mode & 0o222, 0)
        self.assertEqual(
            (promoted / "knowledge.md").stat().st_mode & 0o222,
            0,
        )
        self.assertEqual(
            result["evaluation"]["final_reserved_task_ids"],
            ["final-a", "final-b"],
        )

        wiki = replay_wiki(self.root)
        self.assertEqual(wiki[-1]["kind"], "promotion")

    def test_non_authoritative_evaluation_is_rejected(self):
        evidence = self._sealed_evidence(task_id="source-a")
        candidate = self._candidate(evidence)
        baseline = (
            self._record(
                task_id="promotion-a",
                variant="baseline",
                passed=True,
                latency=1.0,
                authoritative=False,
            ),
        )
        proposed = (
            self._record(
                task_id="promotion-a",
                variant="candidate",
                passed=True,
                latency=1.0,
                candidate=candidate,
            ),
        )
        with self.assertRaisesRegex(LearningError, "must be authoritative"):
            promote_or_reject(
                self.root,
                candidate,
                baseline_records=baseline,
                candidate_records=proposed,
                final_reserved_task_ids=("final-a",),
            )

    def test_skill_candidate_requires_exactly_one_root_skill_md(self):
        evidence = self._sealed_evidence(task_id="source-skill")

        invalid = self.root / "invalid-skill"
        (invalid / "nested").mkdir(parents=True)
        (invalid / "nested" / "SKILL.md").write_text(
            "---\nname: nested\ndescription: invalid\n---\n"
        )
        with self.assertRaisesRegex(
            LearningError,
            "exactly one root SKILL.md",
        ):
            create_candidate(
                self.root,
                kind="skill",
                content_dir=invalid,
                source_evidence=(evidence,),
            )

        valid = self.root / "valid-skill"
        valid.mkdir()
        (valid / "SKILL.md").write_text(
            "---\nname: valid\ndescription: valid\n---\n\n# Skill\n"
        )
        manifest = create_candidate(
            self.root,
            kind="skill",
            content_dir=valid,
            source_evidence=(evidence,),
        )
        data = json.loads(manifest.read_text())
        self.assertEqual(data["kind"], "skill")

    def test_cli_evaluation_schema_rejects_secret_like_extra_fields(self):
        from ctfh_learning.cli import _load_evaluation

        path = self.root / "evaluation.json"
        path.write_text(
            json.dumps(
                {
                    "baseline": [
                        {
                            "task_id": "promotion-a",
                            "passed": True,
                            "authoritative": True,
                            "authoritative_result_s": 1.0,
                            "evaluator_version": "eval-v1",
                            "runlock_base_hash": RUNLOCK_HASH,
                            "expected_candidate": "SECRET",
                        }
                    ],
                    "candidate": [
                        {
                            "task_id": "promotion-a",
                            "passed": True,
                            "authoritative": True,
                            "authoritative_result_s": 1.0,
                            "evaluator_version": "eval-v1",
                            "runlock_base_hash": RUNLOCK_HASH,
                            "candidate_artifact_digest": "e" * 64,
                        }
                    ],
                }
            )
        )
        with self.assertRaisesRegex(
            LearningError,
            "forbidden/unknown fields",
        ):
            _load_evaluation(path)

    def test_core_does_not_import_learning_package(self):
        core = Path(__file__).resolve().parents[1] / "src" / "ctfh_core"
        offenders = []
        for path in core.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "ctfh_learning" in text:
                offenders.append(path.name)
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
