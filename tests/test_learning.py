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
from ctfh_learning.util import canonical_json, sha256_bytes, tree_snapshot_digest


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

    def _write_runlock(
        self,
        *,
        task_id: str,
        variant: str,
        candidate: Path | None,
        treatment: str = "learning-eval",
        mutate: dict[str, object] | None = None,
        candidate_digest_override: str | None = None,
    ) -> tuple[Path, str]:
        base: dict[str, object] = {
            "task_id": task_id,
            "worker_environment": "local-bwrap-pidns-v0",
            "agent": {
                "adapter": "codex-cli",
                "model": "gpt-6-sol",
                "reasoning_effort": "high",
            },
            "capabilities": {
                "policy": "static-narrow-only-v3",
                "admitted": ["native-shell", "workspace-files"],
            },
            "environment_services": {
                "browser": "disabled",
                "interactive-session": "disabled",
            },
            "skills": {
                "mode": "host-pinned",
                "digest": "b" * 64,
                "read_only": True,
                "materializer": "codex-native-copy-v1",
            },
            "memory": {
                "mode": "off",
                "knowledge_snapshot_digest": None,
                "native_memory_isolated": True,
                "read_only": True,
                "materializer": "none",
            },
            "prompt_sha256": "0" * 64,
            "prompt_base_sha256": "0" * 64,
            "prompt_policy": "none",
            "browser_policy": {
                "allowed_domains": [],
                "profile": "disposable-attempt-local",
                "cross_attempt_reuse": False,
            },
            "outcome_authority": "local-exact-candidate-v0",
        }
        if variant == "candidate":
            if candidate is None:
                raise AssertionError("candidate runlock requires candidate manifest")
            data = json.loads(candidate.read_text())
            digest = str(data["content_digest"])
            actual_digest = candidate_digest_override or digest
            if data["kind"] == "knowledge":
                base["memory"] = {
                    "mode": "frozen",
                    "knowledge_snapshot_digest": actual_digest,
                    "native_memory_isolated": True,
                    "read_only": True,
                    "materializer": "snapshot-copy-v1",
                }
                base["prompt_sha256"] = "1" * 64
                base["prompt_policy"] = "frozen-knowledge-guidance-v1"
            else:
                base["skills"] = {
                    "mode": "candidate",
                    "digest": actual_digest,
                    "read_only": True,
                    "materializer": "candidate-copy-v1",
                }

        def merge(dst: dict[str, object], patch: dict[str, object]) -> None:
            for key, value in patch.items():
                current = dst.get(key)
                if isinstance(current, dict) and isinstance(value, dict):
                    merge(current, value)
                else:
                    dst[key] = value

        if mutate:
            merge(base, mutate)
        base_hash = sha256_bytes(canonical_json(base))
        payload: dict[str, object] = {
            "treatment": treatment,
            "base_config": base,
            "base_hash": base_hash,
        }
        payload["full_hash"] = sha256_bytes(
            canonical_json({"treatment": treatment, "base": base})
        )
        root = self.root / "runlocks"
        root.mkdir(exist_ok=True)
        path = root / f"{task_id}-{variant}-{len(list(root.glob('*.json'))):04d}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        path.chmod(0o444)
        return path, base_hash

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
        runlock_mutate: dict[str, object] | None = None,
        runlock_candidate_digest: str | None = None,
        treatment: str = "learning-eval",
    ) -> EvaluationRecord:
        digest = (
            artifact_digest
            if variant == "candidate" and artifact_digest is not None
            else (
                self._candidate_digest(candidate)
                if variant == "candidate"
                else None
            )
        )
        runlock_path, runlock_hash = self._write_runlock(
            task_id=task_id,
            variant=variant,
            candidate=candidate,
            treatment=treatment,
            mutate=runlock_mutate,
            candidate_digest_override=runlock_candidate_digest,
        )
        return EvaluationRecord(
            task_id=task_id,
            variant=variant,
            passed=passed,
            authoritative=authoritative,
            authoritative_result_s=latency,
            evaluator_version=evaluator_version,
            runlock_base_hash=runlock_hash,
            runlock_path=str(runlock_path),
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

    def test_tampered_runlock_file_is_rejected(self):
        evidence = self._sealed_evidence(task_id="source-a")
        candidate = self._candidate(evidence)
        baseline = self._record(
            task_id="promotion-a", variant="baseline", passed=True, latency=1.0
        )
        proposed = self._record(
            task_id="promotion-a", variant="candidate", passed=True, latency=1.0, candidate=candidate
        )
        path = Path(proposed.runlock_path)
        data = json.loads(path.read_text())
        data["base_config"]["agent"]["model"] = "tampered-model"
        path.chmod(0o644)
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        path.chmod(0o444)
        with self.assertRaisesRegex(LearningError, "runlock base_hash mismatch"):
            promote_or_reject(
                self.root, candidate,
                baseline_records=(baseline,), candidate_records=(proposed,),
                final_reserved_task_ids=("final-a",),
            )

    def test_non_treatment_runlock_mismatch_is_rejected(self):
        evidence = self._sealed_evidence(task_id="source-a")
        candidate = self._candidate(evidence)
        baseline = self._record(
            task_id="promotion-a", variant="baseline", passed=True, latency=1.0
        )
        proposed = self._record(
            task_id="promotion-a", variant="candidate", passed=True, latency=1.0, candidate=candidate,
            runlock_mutate={"agent": {"model": "different-model"}},
        )
        with self.assertRaisesRegex(LearningError, "non-treatment RunLock settings differ"):
            promote_or_reject(
                self.root, candidate,
                baseline_records=(baseline,), candidate_records=(proposed,),
                final_reserved_task_ids=("final-a",),
            )

    def test_candidate_runlock_must_pin_exact_candidate_digest(self):
        evidence = self._sealed_evidence(task_id="source-a")
        candidate = self._candidate(evidence)
        baseline = self._record(
            task_id="promotion-a", variant="baseline", passed=True, latency=1.0
        )
        proposed = self._record(
            task_id="promotion-a", variant="candidate", passed=True, latency=1.0, candidate=candidate,
            runlock_candidate_digest="e" * 64,
        )
        with self.assertRaisesRegex(LearningError, "wrong knowledge digest"):
            promote_or_reject(
                self.root, candidate,
                baseline_records=(baseline,), candidate_records=(proposed,),
                final_reserved_task_ids=("final-a",),
            )

    def test_wiki_reference_inherits_final_reserved_provenance(self):
        direct = self._sealed_evidence(task_id="source-direct")
        reserved = self._sealed_evidence(task_id="final-from-wiki")
        event = append_wiki_event(
            self.root,
            kind="hypothesis",
            body={"claim": "reserved-derived hypothesis"},
            evidence=(reserved,),
            tags=("inherited",),
        )
        content = self.root / "wiki-inherited-content"
        content.mkdir()
        (content / "knowledge.md").write_text("WIKI-INHERITED\n")
        candidate = create_candidate(
            self.root,
            kind="knowledge",
            content_dir=content,
            source_evidence=(direct,),
            wiki_event_hashes=(str(event["event_hash"]),),
        )
        data = json.loads(candidate.read_text())
        self.assertEqual(
            data["source_task_ids"],
            ["final-from-wiki", "source-direct"],
        )
        self.assertIn(
            reserved.manifest_sha256,
            data["source_trace_hashes"],
        )

        baseline = (
            self._record(
                task_id="promotion-wiki",
                variant="baseline",
                passed=True,
                latency=1.0,
            ),
        )
        proposed = (
            self._record(
                task_id="promotion-wiki",
                variant="candidate",
                passed=True,
                latency=1.0,
                candidate=candidate,
            ),
        )
        with self.assertRaisesRegex(
            LearningError,
            "source-trace tasks overlap final untouched",
        ):
            promote_or_reject(
                self.root,
                candidate,
                baseline_records=baseline,
                candidate_records=proposed,
                final_reserved_task_ids=("final-from-wiki",),
            )

    def test_parent_candidate_inherits_final_reserved_provenance(self):
        parent_evidence = self._sealed_evidence(task_id="final-from-parent")
        parent_content = self.root / "parent-content"
        parent_content.mkdir()
        (parent_content / "knowledge.md").write_text("PARENT\n")
        parent = create_candidate(
            self.root,
            kind="knowledge",
            content_dir=parent_content,
            source_evidence=(parent_evidence,),
        )
        parent_data = json.loads(parent.read_text())

        child_evidence = self._sealed_evidence(task_id="source-child")
        child_content = self.root / "child-content"
        child_content.mkdir()
        (child_content / "knowledge.md").write_text("CHILD\n")
        child = create_candidate(
            self.root,
            kind="knowledge",
            content_dir=child_content,
            source_evidence=(child_evidence,),
            parent_digest=str(parent_data["content_digest"]),
        )
        child_data = json.loads(child.read_text())
        self.assertEqual(
            child_data["source_task_ids"],
            ["final-from-parent", "source-child"],
        )
        self.assertIn(
            parent_evidence.manifest_sha256,
            child_data["source_trace_hashes"],
        )

        baseline = (
            self._record(
                task_id="promotion-parent",
                variant="baseline",
                passed=True,
                latency=1.0,
            ),
        )
        proposed = (
            self._record(
                task_id="promotion-parent",
                variant="candidate",
                passed=True,
                latency=1.0,
                candidate=child,
            ),
        )
        with self.assertRaisesRegex(
            LearningError,
            "source-trace tasks overlap final untouched",
        ):
            promote_or_reject(
                self.root,
                child,
                baseline_records=baseline,
                candidate_records=proposed,
                final_reserved_task_ids=("final-from-parent",),
            )

    def test_unresolved_provenance_references_fail_closed(self):
        evidence = self._sealed_evidence(task_id="source-a")

        wiki_content = self.root / "bad-wiki-content"
        wiki_content.mkdir()
        (wiki_content / "knowledge.md").write_text("BAD-WIKI\n")
        with self.assertRaisesRegex(
            LearningError,
            "unresolved wiki event reference",
        ):
            create_candidate(
                self.root,
                kind="knowledge",
                content_dir=wiki_content,
                source_evidence=(evidence,),
                wiki_event_hashes=("e" * 64,),
            )

        parent_content = self.root / "bad-parent-content"
        parent_content.mkdir()
        (parent_content / "knowledge.md").write_text("BAD-PARENT\n")
        with self.assertRaisesRegex(
            LearningError,
            "unresolved parent candidate digest",
        ):
            create_candidate(
                self.root,
                kind="knowledge",
                content_dir=parent_content,
                source_evidence=(evidence,),
                parent_digest="f" * 64,
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
                            "runlock_base_hash": "d" * 64,
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
                            "runlock_base_hash": "d" * 64,
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
