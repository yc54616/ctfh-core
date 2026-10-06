from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from ctfh_core.intent_gate import (
    IntentGateError,
    _event_files,
    _event_hash,
    _validate_event_shape,
    append_event,
    approve_evidence,
    brief,
    build_candidate_event,
    converge,
    evidence_hash,
    preflight,
    resolve,
)


def atom(
    atom_id: str,
    key: str,
    *,
    authority: str = "agent",
    kind: str = "requirement",
    scope: list[str] | None = None,
    statement: str | None = None,
    verification: dict[str, str] | None = None,
    **extra,
):
    out = {
        "id": atom_id,
        "key": key,
        "kind": kind,
        "authority": authority,
        "scope": {"paths": scope or []},
        "polarity": "require",
        "statement": statement or f"require {key}",
        "verification": verification or {"kind": "manual", "ref": "review"},
    }
    out.update(extra)
    return out


def write_event(
    root: Path,
    *,
    seq: int,
    prev: str | None,
    actor: str,
    ops: list[dict],
    approve: bool = False,
    note: str = "",
) -> str:
    event = _validate_event_shape(
        {"v": 1, "seq": seq, "prev": prev, "actor": actor, "ops": ops, "note": note}
    )
    event_hash = _event_hash(event)
    events = root / ".intent" / "events"
    approvals = root / ".intent" / "approvals"
    generated = root / ".intent" / "generated"
    events.mkdir(parents=True, exist_ok=True)
    approvals.mkdir(parents=True, exist_ok=True)
    generated.mkdir(parents=True, exist_ok=True)
    (events / f"{seq:04d}-event.yaml").write_text(
        yaml.safe_dump(event, sort_keys=False, allow_unicode=True)
    )
    if approve:
        (approvals / f"{seq:04d}-{event_hash[:12]}.yaml").write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "event_hash": event_hash,
                    "approver": "test-principal",
                    "channel": "test",
                },
                sort_keys=False,
            )
        )
    return event_hash


def make_ledger(root: Path, atoms: list[dict]) -> str:
    return write_event(
        root,
        seq=0,
        prev=None,
        actor="principal",
        ops=[{"type": "ADD", "atom": a} for a in atoms],
        approve=True,
        note="genesis",
    )


def init_git(root: Path) -> str:
    (root / ".gitignore").write_text(
        ".intent/evidence-approvals/\n.intent/generated/\n"
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Intent Gate Test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-qm", "baseline"],
        check=True,
    )
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def write_evidence(
    root: Path,
    *,
    result,
    results: list[dict],
    paths: list[str] | None = None,
    execute_commands: bool = False,
) -> Path:
    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    path = root.parent / f"{root.name}-evidence-{len(list(root.parent.glob(root.name + '-evidence-*')))}.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "contract_hash": result.contract_hash,
                "ledger_head": result.ledger_head,
                "commit": commit,
                "paths": paths or [],
                "execute_commands": execute_commands,
                "results": results,
            },
            sort_keys=False,
        )
    )
    return path


def approve_test_evidence(root: Path, evidence_path: Path) -> str:
    payload = yaml.safe_load(evidence_path.read_text())
    digest = evidence_hash(payload)
    approve_evidence(
        root,
        evidence_path=evidence_path,
        approver="test-principal",
        channel="test",
        approved_evidence_hash=digest,
    )
    return digest


class IntentGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_genesis_replays_and_cache_matches(self):
        make_ledger(
            self.root,
            [
                atom(
                    "INT-0001",
                    "project.objective",
                    authority="principal",
                    kind="intent",
                ),
                atom("REQ-0001", "feature.one"),
            ],
        )
        result = resolve(self.root, write_cache=True)
        self.assertEqual(result.event_count, 1)
        self.assertEqual(len(result.active_atoms), 2)
        report = preflight(self.root, refs=["REQ-0001"], require_cache=True)
        self.assertTrue(report.ok, report.errors)

    def test_tampered_principal_event_invalidates_approval(self):
        make_ledger(
            self.root,
            [atom("INT-0001", "project.objective", authority="principal", kind="intent")],
        )
        event_path = next((self.root / ".intent" / "events").glob("*.yaml"))
        data = yaml.safe_load(event_path.read_text())
        data["ops"][0]["atom"]["statement"] = "tampered"
        event_path.write_text(yaml.safe_dump(data, sort_keys=False))
        with self.assertRaises(IntentGateError):
            resolve(self.root)

    def test_principal_event_without_matching_approval_fails(self):
        write_event(
            self.root,
            seq=0,
            prev=None,
            actor="principal",
            ops=[
                {
                    "type": "ADD",
                    "atom": atom(
                        "INT-0001",
                        "project.objective",
                        authority="principal",
                        kind="intent",
                    ),
                }
            ],
            approve=False,
        )
        with self.assertRaises(IntentGateError):
            resolve(self.root)

    def test_agent_cannot_modify_principal_atom(self):
        make_ledger(
            self.root,
            [atom("INV-0001", "guard", authority="principal", kind="invariant")],
        )
        head = resolve(self.root).ledger_head
        with self.assertRaises(IntentGateError):
            build_candidate_event(
                root=self.root,
                actor="agent",
                proposal={
                    "base": head,
                    "ops": [{"type": "CANCEL", "ids": ["INV-0001"]}],
                },
            )

    def test_agent_add_then_cancel(self):
        make_ledger(
            self.root,
            [atom("INT-0001", "objective", authority="principal", kind="intent")],
        )
        head = resolve(self.root).ledger_head
        p1 = self.root / "add.yaml"
        p1.write_text(
            yaml.safe_dump(
                {
                    "base": head,
                    "ops": [{"type": "ADD", "atom": atom("REQ-0001", "feature.one")}],
                },
                sort_keys=False,
            )
        )
        append_event(root=self.root, proposal_path=p1, actor="agent")
        r1 = resolve(self.root)
        self.assertIn("REQ-0001", r1.active_by_id())

        p2 = self.root / "cancel.yaml"
        p2.write_text(
            yaml.safe_dump(
                {
                    "base": r1.ledger_head,
                    "ops": [{"type": "CANCEL", "ids": ["REQ-0001"]}],
                },
                sort_keys=False,
            )
        )
        append_event(root=self.root, proposal_path=p2, actor="agent")
        r2 = resolve(self.root)
        self.assertNotIn("REQ-0001", r2.active_by_id())
        self.assertEqual(r2.statuses["REQ-0001"], "cancelled")

    def test_stale_proposal_base_rejected(self):
        make_ledger(self.root, [atom("REQ-0001", "feature.one")])
        with self.assertRaises(IntentGateError):
            build_candidate_event(
                root=self.root,
                actor="agent",
                proposal={
                    "base": "0" * 64,
                    "ops": [{"type": "ADD", "atom": atom("REQ-0002", "feature.two")}],
                },
            )

    def test_key_scope_collision_requires_supersede(self):
        make_ledger(
            self.root,
            [atom("REQ-0001", "worker.mode", scope=["src"])],
        )
        head = resolve(self.root).ledger_head
        with self.assertRaises(IntentGateError):
            build_candidate_event(
                root=self.root,
                actor="agent",
                proposal={
                    "base": head,
                    "ops": [
                        {
                            "type": "ADD",
                            "atom": atom(
                                "REQ-0002",
                                "worker.mode",
                                scope=["src/core"],
                                statement="different mode",
                            ),
                        }
                    ],
                },
            )

    def test_partial_scope_supersede_is_deferred(self):
        make_ledger(
            self.root,
            [atom("REQ-0001", "worker.mode", scope=["src"])],
        )
        head = resolve(self.root).ledger_head
        with self.assertRaises(IntentGateError):
            build_candidate_event(
                root=self.root,
                actor="agent",
                proposal={
                    "base": head,
                    "ops": [
                        {
                            "type": "SUPERSEDE",
                            "old_ids": ["REQ-0001"],
                            "new_atoms": [
                                atom(
                                    "REQ-0002",
                                    "worker.mode",
                                    scope=["src/core"],
                                    statement="narrower mode",
                                )
                            ],
                        }
                    ],
                },
            )

    def test_path_invariance_hash_ignores_atom_ids_and_history(self):
        other_tmp = tempfile.TemporaryDirectory()
        other = Path(other_tmp.name)
        try:
            make_ledger(
                self.root,
                [atom("REQ-0001", "same.key", statement="Same semantic contract")],
            )
            make_ledger(
                other,
                [atom("REQ-9999", "same.key", statement="Same semantic contract")],
            )
            left = resolve(self.root)
            right = resolve(other)
            self.assertEqual(left.contract_hash, right.contract_hash)
            self.assertNotEqual(left.ledger_head, right.ledger_head)
        finally:
            other_tmp.cleanup()

    def test_same_new_id_cannot_be_reused_inside_one_event(self):
        make_ledger(self.root, [atom("REQ-0001", "base.one")])
        head = resolve(self.root).ledger_head
        event = {
            "v": 1,
            "seq": 1,
            "prev": head,
            "actor": "agent",
            "ops": [
                {"type": "ADD", "atom": atom("REQ-0002", "feature.one")},
                {"type": "ADD", "atom": atom("REQ-0002", "feature.two")},
            ],
            "note": "duplicate id in atomic event",
        }
        write_event(
            self.root,
            seq=1,
            prev=head,
            actor="agent",
            ops=event["ops"],
        )
        with self.assertRaises(IntentGateError):
            resolve(self.root)

    def test_same_active_atom_cannot_be_targeted_twice_in_one_event(self):
        make_ledger(self.root, [atom("REQ-0001", "base.one")])
        head = resolve(self.root).ledger_head
        write_event(
            self.root,
            seq=1,
            prev=head,
            actor="agent",
            ops=[
                {"type": "CANCEL", "ids": ["REQ-0001"]},
                {"type": "CANCEL", "ids": ["REQ-0001"]},
            ],
        )
        with self.assertRaises(IntentGateError):
            resolve(self.root)

    def test_wrong_prev_rejects_fork(self):
        head = make_ledger(self.root, [atom("REQ-0001", "feature.one")])
        write_event(
            self.root,
            seq=1,
            prev="f" * 64,
            actor="agent",
            ops=[{"type": "ADD", "atom": atom("REQ-0002", "feature.two")}],
        )
        self.assertNotEqual(head, "f" * 64)
        with self.assertRaises(IntentGateError):
            resolve(self.root)

    def test_decision_body_tamper_is_detected(self):
        decision_dir = self.root / ".intent" / "decisions"
        decision_dir.mkdir(parents=True)
        body = decision_dir / "0001.md"
        body.write_text("# Decision\noriginal\n")
        digest = hashlib.sha256(body.read_bytes()).hexdigest()
        make_ledger(
            self.root,
            [
                atom(
                    "DEC-0001",
                    "decision.one",
                    authority="principal",
                    kind="decision",
                    statement="Use decision one",
                    body_ref=".intent/decisions/0001.md",
                    body_hash=digest,
                )
            ],
        )
        resolve(self.root)
        body.write_text("# Decision\ntampered\n")
        with self.assertRaises(IntentGateError):
            resolve(self.root)

    def test_decision_body_must_live_under_intent_decisions(self):
        outside = self.root / "outside-decision.md"
        outside.write_text("# Outside\n")
        digest = hashlib.sha256(outside.read_bytes()).hexdigest()
        with self.assertRaises(IntentGateError):
            make_ledger(
                self.root,
                [
                    atom(
                        "DEC-0001",
                        "decision.outside",
                        authority="principal",
                        kind="decision",
                        body_ref="outside-decision.md",
                        body_hash=digest,
                    )
                ],
            )

    def test_decision_body_symlink_escape_is_rejected(self):
        decision_dir = self.root / ".intent" / "decisions"
        decision_dir.mkdir(parents=True)
        outside = self.root / "outside-decision.md"
        outside.write_text("# Outside\n")
        link = decision_dir / "linked.md"
        link.symlink_to(outside)
        digest = hashlib.sha256(outside.read_bytes()).hexdigest()
        make_ledger(
            self.root,
            [
                atom(
                    "DEC-0001",
                    "decision.symlink",
                    authority="principal",
                    kind="decision",
                    body_ref=".intent/decisions/linked.md",
                    body_hash=digest,
                )
            ],
        )
        with self.assertRaises(IntentGateError):
            resolve(self.root)

    def test_path_normalization_is_idempotent_for_dot_prefixes(self):
        from ctfh_core.intent_gate import _normalize_path_prefix

        once = _normalize_path_prefix("./  src/module")
        twice = _normalize_path_prefix(once)
        self.assertEqual(once, twice)
        with self.assertRaises(IntentGateError):
            _normalize_path_prefix("./ /absolute-after-trim")

    def test_brief_is_scope_aware_but_keeps_principal(self):
        make_ledger(
            self.root,
            [
                atom(
                    "INV-0001",
                    "global.guard",
                    authority="principal",
                    kind="invariant",
                ),
                atom("REQ-0001", "source.rule", scope=["src"]),
                atom("REQ-0002", "docs.rule", scope=["docs"]),
            ],
        )
        text = brief(self.root, paths=["src/ctfh_core/file.py"])
        self.assertIn("INV-0001", text)
        self.assertIn("REQ-0001", text)
        self.assertNotIn("REQ-0002", text)

    def test_convergence_is_bound_to_contract_head_and_commit(self):
        make_ledger(
            self.root,
            [atom("INV-0001", "guard", authority="principal", kind="invariant")],
        )
        init_git(self.root)
        result = resolve(self.root)
        evidence = write_evidence(
            self.root,
            result=result,
            execute_commands=False,
            results=[
                {
                    "atom_id": "INV-0001",
                    "status": "passed",
                    "actor": "principal",
                    "evidence": "reviewed",
                }
            ],
        )
        approve_test_evidence(self.root, evidence)
        report = converge(
            self.root,
            evidence_path=evidence,
            execute_commands=False,
        )
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["paths"], [])
        self.assertFalse(report["execute_commands"])
        self.assertEqual(report["evidence_hash"], evidence_hash(yaml.safe_load(evidence.read_text())))

        bad = yaml.safe_load(evidence.read_text())
        bad["contract_hash"] = "0" * 64
        evidence.write_text(yaml.safe_dump(bad, sort_keys=False))
        with self.assertRaises(IntentGateError):
            converge(self.root, evidence_path=evidence, execute_commands=False)

    def test_deferred_or_not_run_never_counts_as_pass(self):
        make_ledger(self.root, [atom("REQ-0001", "feature.one")])
        init_git(self.root)
        result = resolve(self.root)
        for status in ("deferred", "open", "not_run", "failed"):
            evidence = write_evidence(
                self.root,
                result=result,
                execute_commands=False,
                results=[
                    {
                        "atom_id": "REQ-0001",
                        "status": status,
                        "actor": "agent",
                        "evidence": status,
                    }
                ],
            )
            report = converge(
                self.root,
                evidence_path=evidence,
                execute_commands=False,
            )
            self.assertEqual(report["status"], "BLOCKED", status)
            evidence.unlink(missing_ok=True)

    def test_invalid_approval_shape_is_rejected(self):
        make_ledger(
            self.root,
            [atom("INT-0001", "objective", authority="principal", kind="intent")],
        )
        approval = next((self.root / ".intent" / "approvals").glob("*.yaml"))
        data = yaml.safe_load(approval.read_text())
        data.pop("channel")
        approval.write_text(yaml.safe_dump(data, sort_keys=False))
        with self.assertRaises(IntentGateError):
            resolve(self.root)

    def test_supersede_is_one_to_one_and_preserves_key(self):
        make_ledger(self.root, [atom("REQ-0001", "worker.mode")])
        head = resolve(self.root).ledger_head

        with self.assertRaises(IntentGateError):
            build_candidate_event(
                root=self.root,
                actor="agent",
                proposal={
                    "base": head,
                    "ops": [
                        {
                            "type": "SUPERSEDE",
                            "old_ids": ["REQ-0001"],
                            "new_atoms": [
                                atom("REQ-0002", "worker.mode"),
                                atom("REQ-0003", "worker.mode"),
                            ],
                        }
                    ],
                },
            )

        with self.assertRaises(IntentGateError):
            build_candidate_event(
                root=self.root,
                actor="agent",
                proposal={
                    "base": head,
                    "ops": [
                        {
                            "type": "SUPERSEDE",
                            "old_ids": ["REQ-0001"],
                            "new_atoms": [
                                atom("REQ-0002", "different.key"),
                            ],
                        }
                    ],
                },
            )

    def test_principal_manual_pass_requires_principal_actor(self):
        make_ledger(
            self.root,
            [atom("INV-0001", "guard", authority="principal", kind="invariant")],
        )
        init_git(self.root)
        result = resolve(self.root)
        evidence = write_evidence(
            self.root,
            result=result,
            execute_commands=False,
            results=[
                {
                    "atom_id": "INV-0001",
                    "status": "passed",
                    "actor": "agent",
                    "evidence": "self-attested",
                }
            ],
        )
        report = converge(
            self.root,
            evidence_path=evidence,
            execute_commands=False,
        )
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["results"][0]["status"], "failed")

    def test_append_rolls_back_event_and_approval_on_post_write_failure(self):
        make_ledger(
            self.root,
            [atom("INV-0001", "guard", authority="principal", kind="invariant")],
        )
        current = resolve(self.root)
        proposal = self.root / "principal-change.yaml"
        proposal.write_text(
            yaml.safe_dump(
                {
                    "base": current.ledger_head,
                    "ops": [
                        {
                            "type": "SUPERSEDE",
                            "old_ids": ["INV-0001"],
                            "new_atoms": [
                                atom(
                                    "INV-0002",
                                    "guard",
                                    authority="principal",
                                    kind="invariant",
                                    statement="updated guard",
                                )
                            ],
                        }
                    ],
                },
                sort_keys=False,
            )
        )
        _, approved_hash, _ = build_candidate_event(
            root=self.root,
            proposal=yaml.safe_load(proposal.read_text()),
            actor="principal",
        )
        events_before = set((self.root / ".intent" / "events").glob("*.yaml"))
        approvals_before = set((self.root / ".intent" / "approvals").glob("*.yaml"))
        with patch(
            "ctfh_core.intent_gate.write_active_cache",
            side_effect=IntentGateError("simulated cache failure"),
        ):
            with self.assertRaises(IntentGateError):
                append_event(
                    root=self.root,
                    proposal_path=proposal,
                    actor="principal",
                    approver="test-principal",
                    confirmed=True,
                    approved_event_hash=approved_hash,
                )
        self.assertEqual(
            set((self.root / ".intent" / "events").glob("*.yaml")),
            events_before,
        )
        self.assertEqual(
            set((self.root / ".intent" / "approvals").glob("*.yaml")),
            approvals_before,
        )

    def test_generated_cache_mismatch_is_detected(self):
        make_ledger(self.root, [atom("REQ-0001", "feature.one")])
        resolve(self.root, write_cache=True)
        cache = self.root / ".intent" / "generated" / "active-contract.json"
        data = json.loads(cache.read_text())
        data["contract_hash"] = "0" * 64
        cache.write_text(json.dumps(data))
        report = preflight(self.root, refs=["REQ-0001"], require_cache=True)
        self.assertFalse(report.ok)
        self.assertTrue(any("cache" in e for e in report.errors))

    def test_git_tracked_history_edit_is_rejected_by_preflight(self):
        make_ledger(self.root, [atom("REQ-0001", "feature.one")])
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(self.root), "add", ".intent"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "intent"], check=True)
        event = next((self.root / ".intent" / "events").glob("*.yaml"))
        data = yaml.safe_load(event.read_text())
        data["note"] = "edited history"
        event.write_text(yaml.safe_dump(data, sort_keys=False))
        report = preflight(self.root, refs=["REQ-0001"])
        self.assertFalse(report.ok)
        self.assertTrue(any("history" in e for e in report.errors))


    def test_principal_confirmation_is_bound_to_exact_candidate_hash(self):
        make_ledger(
            self.root,
            [atom("INV-0001", "guard", authority="principal", kind="invariant")],
        )
        current = resolve(self.root)
        proposal = self.root / "principal-change.yaml"
        proposal_data = {
            "base": current.ledger_head,
            "ops": [
                {
                    "type": "SUPERSEDE",
                    "old_ids": ["INV-0001"],
                    "new_atoms": [
                        atom(
                            "INV-0002",
                            "guard",
                            authority="principal",
                            kind="invariant",
                            statement="first approved wording",
                        )
                    ],
                }
            ],
        }
        proposal.write_text(yaml.safe_dump(proposal_data, sort_keys=False))
        _, approved_hash, _ = build_candidate_event(
            root=self.root,
            proposal=proposal_data,
            actor="principal",
        )

        proposal_data["ops"][0]["new_atoms"][0]["statement"] = "changed after approval"
        proposal.write_text(yaml.safe_dump(proposal_data, sort_keys=False))

        with self.assertRaises(IntentGateError):
            append_event(
                root=self.root,
                proposal_path=proposal,
                actor="principal",
                approver="test-principal",
                confirmed=True,
                approved_event_hash=approved_hash,
            )

        self.assertEqual(len(list((self.root / ".intent" / "events").glob("*.yaml"))), 1)

    def test_convergence_rejects_dirty_git_worktree(self):
        make_ledger(
            self.root,
            [atom("INV-0001", "guard", authority="principal", kind="invariant")],
        )
        tracked = self.root / "implementation.txt"
        tracked.write_text("v1")
        init_git(self.root)
        result = resolve(self.root)
        evidence = write_evidence(
            self.root,
            result=result,
            execute_commands=False,
            results=[
                {
                    "atom_id": "INV-0001",
                    "status": "passed",
                    "actor": "principal",
                    "evidence": "reviewed",
                }
            ],
        )
        approve_test_evidence(self.root, evidence)
        report = converge(
            self.root,
            evidence_path=evidence,
            execute_commands=False,
        )
        self.assertEqual(report["status"], "PASS")

        tracked.write_text("v2")
        with self.assertRaises(IntentGateError):
            converge(
                self.root,
                evidence_path=evidence,
                execute_commands=False,
            )

    def test_convergence_requires_git_commit(self):
        make_ledger(self.root, [atom("REQ-0001", "feature.one")])
        result = resolve(self.root)
        evidence = self.root.parent / f"{self.root.name}-nogit.yaml"
        evidence.write_text(
            yaml.safe_dump(
                {
                    "contract_hash": result.contract_hash,
                    "ledger_head": result.ledger_head,
                    "commit": "0" * 40,
                    "paths": [],
                    "execute_commands": False,
                    "results": [],
                },
                sort_keys=False,
            )
        )
        with self.assertRaises(IntentGateError):
            converge(self.root, evidence_path=evidence, execute_commands=False)

    def test_principal_manual_pass_requires_hash_bound_approval(self):
        make_ledger(
            self.root,
            [atom("INV-0001", "guard", authority="principal", kind="invariant")],
        )
        init_git(self.root)
        result = resolve(self.root)
        evidence = write_evidence(
            self.root,
            result=result,
            execute_commands=False,
            results=[
                {
                    "atom_id": "INV-0001",
                    "status": "passed",
                    "actor": "principal",
                    "evidence": "reviewed",
                }
            ],
        )
        with self.assertRaises(IntentGateError):
            converge(self.root, evidence_path=evidence, execute_commands=False)

        digest = approve_test_evidence(self.root, evidence)
        report = converge(self.root, evidence_path=evidence, execute_commands=False)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["evidence_hash"], digest)

        changed = yaml.safe_load(evidence.read_text())
        changed["results"][0]["evidence"] = "changed after approval"
        evidence.write_text(yaml.safe_dump(changed, sort_keys=False))
        with self.assertRaises(IntentGateError):
            converge(self.root, evidence_path=evidence, execute_commands=False)

    def test_root_path_includes_scoped_requirements(self):
        make_ledger(
            self.root,
            [atom("REQ-0001", "source.rule", scope=["src"])],
        )
        init_git(self.root)
        result = resolve(self.root)
        evidence = write_evidence(
            self.root,
            result=result,
            paths=["."],
            execute_commands=False,
            results=[
                {
                    "atom_id": "REQ-0001",
                    "status": "passed",
                    "actor": "agent",
                    "evidence": "checked",
                }
            ],
        )
        report = converge(
            self.root,
            evidence_path=evidence,
            paths=["."],
            execute_commands=False,
        )
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["paths"], [""])
        self.assertIn("REQ-0001", brief(self.root, paths=["."]))

    def test_absolute_task_path_is_rejected(self):
        make_ledger(self.root, [atom("REQ-0001", "feature.one")])
        with self.assertRaises(IntentGateError):
            brief(self.root, paths=[str(self.root / "src")])

    def test_verification_command_that_mutates_tree_cannot_pass(self):
        tracked = self.root / "implementation.txt"
        tracked.write_text("v1")
        make_ledger(
            self.root,
            [
                atom(
                    "REQ-0001",
                    "command.check",
                    verification={
                        "kind": "command",
                        "ref": "printf x >> implementation.txt",
                    },
                )
            ],
        )
        init_git(self.root)
        with self.assertRaises(IntentGateError):
            converge(self.root, execute_commands=True)

    def test_strict_event_shape_rejects_schema_mismatches(self):
        base = {
            "v": 1,
            "seq": 0,
            "prev": None,
            "actor": "agent",
            "ops": [{"type": "ADD", "atom": atom("REQ-0001", "feature.one")}],
            "note": "test",
        }

        cases = []

        x = dict(base)
        x["v"] = True
        cases.append(x)

        x = dict(base)
        x["seq"] = False
        cases.append(x)

        x = dict(base)
        x.pop("note")
        cases.append(x)

        x = dict(base)
        x["extra"] = "ignored?"
        cases.append(x)

        x = yaml.safe_load(yaml.safe_dump(base))
        x["ops"][0]["type"] = "add"
        cases.append(x)

        x = yaml.safe_load(yaml.safe_dump(base))
        x["ops"][0]["extra"] = 1
        cases.append(x)

        x = yaml.safe_load(yaml.safe_dump(base))
        x["ops"][0]["atom"].pop("scope")
        cases.append(x)

        x = yaml.safe_load(yaml.safe_dump(base))
        x["ops"][0]["atom"]["extra"] = 1
        cases.append(x)

        for candidate in cases:
            with self.subTest(candidate=candidate):
                with self.assertRaises(IntentGateError):
                    _validate_event_shape(candidate)

    def test_unicode_equivalent_binding_fields_have_same_contract_hash(self):
        composed = "café"
        decomposed = "cafe\u0301"

        left = self.root / "left"
        right = self.root / "right"
        left.mkdir()
        right.mkdir()
        make_ledger(
            left,
            [
                atom(
                    "REQ-0001",
                    composed,
                    statement=composed,
                    verification={"kind": "manual", "ref": composed},
                )
            ],
        )
        make_ledger(
            right,
            [
                atom(
                    "REQ-9999",
                    decomposed,
                    statement=decomposed,
                    verification={"kind": "manual", "ref": decomposed},
                )
            ],
        )
        self.assertEqual(resolve(left).contract_hash, resolve(right).contract_hash)

    def test_successful_supersede_marks_old_atom_superseded(self):
        make_ledger(self.root, [atom("REQ-0001", "worker.mode")])
        head = resolve(self.root).ledger_head
        proposal = self.root / "supersede.yaml"
        proposal.write_text(
            yaml.safe_dump(
                {
                    "base": head,
                    "note": "update mode",
                    "ops": [
                        {
                            "type": "SUPERSEDE",
                            "old_ids": ["REQ-0001"],
                            "new_atoms": [
                                atom(
                                    "REQ-0002",
                                    "worker.mode",
                                    statement="updated mode",
                                )
                            ],
                        }
                    ],
                },
                sort_keys=False,
            )
        )
        append_event(root=self.root, proposal_path=proposal, actor="agent")
        resolved = resolve(self.root)
        self.assertEqual(resolved.statuses["REQ-0001"], "superseded")
        self.assertEqual(resolved.statuses["REQ-0002"], "active")

    def test_event_files_sort_numerically(self):
        directory = self.root / ".intent" / "events"
        directory.mkdir(parents=True)
        for name in ("10000-z.yaml", "1001-y.yaml", "0000-x.yaml"):
            (directory / name).write_text("{}")
        self.assertEqual(
            [path.name for path in _event_files(self.root)],
            ["0000-x.yaml", "1001-y.yaml", "10000-z.yaml"],
        )

    def test_committed_history_rewrite_is_rejected(self):
        make_ledger(self.root, [atom("REQ-0001", "feature.one")])
        init_git(self.root)

        head = resolve(self.root).ledger_head
        proposal = self.root.parent / f"{self.root.name}-append.yaml"
        proposal.write_text(
            yaml.safe_dump(
                {
                    "base": head,
                    "note": "add second",
                    "ops": [
                        {
                            "type": "ADD",
                            "atom": atom("REQ-0002", "feature.two"),
                        }
                    ],
                },
                sort_keys=False,
            )
        )
        append_event(root=self.root, proposal_path=proposal, actor="agent")
        subprocess.run(["git", "-C", str(self.root), "add", ".intent"], check=True)
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-qm", "append intent event"],
            check=True,
        )

        event_path = sorted((self.root / ".intent" / "events").glob("*.yaml"))[-1]
        event = yaml.safe_load(event_path.read_text())
        event["note"] = "rewritten"
        event_path.write_text(yaml.safe_dump(event, sort_keys=False))
        subprocess.run(["git", "-C", str(self.root), "add", str(event_path)], check=True)
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-qm", "bad rewrite"],
            check=True,
        )

        report = preflight(self.root, refs=[])
        self.assertFalse(report.ok)
        self.assertTrue(
            any("rewritten in git history" in error for error in report.errors),
            report.errors,
        )

    def test_convergence_rechecks_after_verification_commands(self):
        tracked = self.root / "implementation.txt"
        tracked.write_text("v1")
        make_ledger(
            self.root,
            [
                atom(
                    "REQ-0001",
                    "command.check",
                    verification={
                        "kind": "command",
                        "ref": "printf x >> implementation.txt",
                    },
                )
            ],
        )
        init_git(self.root)
        with self.assertRaises(IntentGateError):
            converge(self.root, execute_commands=True)





class FinalBlockerRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_root_marker_with_narrower_scope_stays_global(self):
        make_ledger(
            self.root,
            [atom("REQ-0001", "global.rule", scope=[".", "src"])],
        )
        result = resolve(self.root)
        self.assertEqual(
            result.active_by_id()["REQ-0001"]["scope"]["paths"],
            [],
        )
        self.assertIn(
            "REQ-0001",
            brief(self.root, paths=["docs/readme.md"]),
        )

    def test_convergence_output_rejects_project_paths(self):
        from ctfh_core.cli import _safe_convergence_output

        tracked = self.root / "implementation.py"
        tracked.write_text("original\n")
        with self.assertRaises(IntentGateError):
            _safe_convergence_output(self.root, str(tracked))
        self.assertEqual(tracked.read_text(), "original\n")

        outside = self.root.parent / f"{self.root.name}-report.json"
        self.assertEqual(
            _safe_convergence_output(self.root, str(outside)),
            outside.resolve(),
        )

        hardlink_target = self.root / "tracked-hardlink.txt"
        hardlink_target.write_text("do-not-overwrite\n")
        hardlink_out = self.root.parent / f"{self.root.name}-hardlink-report.json"
        os.link(hardlink_target, hardlink_out)
        try:
            with self.assertRaises(IntentGateError):
                _safe_convergence_output(self.root, str(hardlink_out))
            self.assertEqual(hardlink_target.read_text(), "do-not-overwrite\n")
        finally:
            hardlink_out.unlink(missing_ok=True)

        make_ledger(self.root, [atom("REQ-0001", "feature.one")])
        accepted_event = next((self.root / ".intent" / "events").glob("*.yaml"))
        event_bytes = accepted_event.read_bytes()
        event_alias = self.root.parent / f"{self.root.name}-event-alias.json"
        os.link(accepted_event, event_alias)
        try:
            with self.assertRaises(IntentGateError):
                _safe_convergence_output(self.root, str(event_alias))
            self.assertEqual(accepted_event.read_bytes(), event_bytes)
        finally:
            event_alias.unlink(missing_ok=True)

    def test_merge_commit_history_mutation_is_rejected(self):
        make_ledger(self.root, [atom("REQ-0001", "feature.one")])
        init_git(self.root)
        base_branch = subprocess.run(
            ["git", "-C", str(self.root), "branch", "--show-current"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        subprocess.run(
            ["git", "-C", str(self.root), "checkout", "-qb", "feature"],
            check=True,
        )
        (self.root / "feature.txt").write_text("feature\n")
        subprocess.run(
            ["git", "-C", str(self.root), "add", "feature.txt"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-qm", "feature"],
            check=True,
        )

        subprocess.run(
            ["git", "-C", str(self.root), "checkout", "-q", base_branch],
            check=True,
        )
        (self.root / "main.txt").write_text("main\n")
        subprocess.run(
            ["git", "-C", str(self.root), "add", "main.txt"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-qm", "main"],
            check=True,
        )

        subprocess.run(
            ["git", "-C", str(self.root), "merge", "--no-ff", "--no-commit", "feature"],
            check=True,
            capture_output=True,
            text=True,
        )
        event_path = next((self.root / ".intent" / "events").glob("*.yaml"))
        event = yaml.safe_load(event_path.read_text())
        event["note"] = "mutated only in merge commit"
        event_path.write_text(yaml.safe_dump(event, sort_keys=False))
        subprocess.run(
            ["git", "-C", str(self.root), "add", str(event_path)],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-qm", "merge with mutation"],
            check=True,
        )

        report = preflight(self.root)
        self.assertFalse(report.ok)
        self.assertTrue(
            any("rewritten in git history" in error for error in report.errors),
            report.errors,
        )


    def test_existing_matching_approval_survives_failed_retry(self):
        make_ledger(
            self.root,
            [atom("INV-0001", "guard", authority="principal", kind="invariant")],
        )
        current = resolve(self.root)
        proposal = self.root / "principal-retry.yaml"
        proposal.write_text(
            yaml.safe_dump(
                {
                    "base": current.ledger_head,
                    "ops": [
                        {
                            "type": "SUPERSEDE",
                            "old_ids": ["INV-0001"],
                            "new_atoms": [
                                atom(
                                    "INV-0002",
                                    "guard",
                                    authority="principal",
                                    kind="invariant",
                                    statement="updated guard",
                                )
                            ],
                        }
                    ],
                },
                sort_keys=False,
            )
        )
        _, approved_hash, _ = build_candidate_event(
            root=self.root,
            proposal=yaml.safe_load(proposal.read_text()),
            actor="principal",
        )
        approval_path = (
            self.root
            / ".intent"
            / "approvals"
            / f"0001-{approved_hash[:12]}.yaml"
        )
        approval = {
            "version": 1,
            "event_hash": approved_hash,
            "approver": "test-principal",
            "channel": "interactive-confirmation",
        }
        approval_path.write_text(
            yaml.safe_dump(approval, sort_keys=False),
            encoding="utf-8",
        )
        before = approval_path.read_bytes()

        with patch(
            "ctfh_core.intent_gate.write_active_cache",
            side_effect=IntentGateError("simulated cache failure"),
        ):
            with self.assertRaises(IntentGateError):
                append_event(
                    root=self.root,
                    proposal_path=proposal,
                    actor="principal",
                    approver="test-principal",
                    confirmed=True,
                    approved_event_hash=approved_hash,
                )

        self.assertTrue(approval_path.is_file())
        self.assertEqual(approval_path.read_bytes(), before)
        self.assertFalse(
            any(
                p.name.startswith("0001-")
                for p in (self.root / ".intent" / "events").glob("*.yaml")
            )
        )

    def test_conflicting_existing_approval_is_not_overwritten(self):
        make_ledger(
            self.root,
            [atom("INV-0001", "guard", authority="principal", kind="invariant")],
        )
        current = resolve(self.root)
        proposal = self.root / "principal-conflict.yaml"
        proposal.write_text(
            yaml.safe_dump(
                {
                    "base": current.ledger_head,
                    "ops": [
                        {
                            "type": "SUPERSEDE",
                            "old_ids": ["INV-0001"],
                            "new_atoms": [
                                atom(
                                    "INV-0002",
                                    "guard",
                                    authority="principal",
                                    kind="invariant",
                                    statement="updated guard",
                                )
                            ],
                        }
                    ],
                },
                sort_keys=False,
            )
        )
        _, approved_hash, _ = build_candidate_event(
            root=self.root,
            proposal=yaml.safe_load(proposal.read_text()),
            actor="principal",
        )
        approval_path = (
            self.root
            / ".intent"
            / "approvals"
            / f"0001-{approved_hash[:12]}.yaml"
        )
        conflicting = {
            "version": 1,
            "event_hash": approved_hash,
            "approver": "different-principal",
            "channel": "interactive-confirmation",
        }
        approval_path.write_text(
            yaml.safe_dump(conflicting, sort_keys=False),
            encoding="utf-8",
        )
        before = approval_path.read_bytes()

        with self.assertRaises(IntentGateError):
            append_event(
                root=self.root,
                proposal_path=proposal,
                actor="principal",
                approver="test-principal",
                confirmed=True,
                approved_event_hash=approved_hash,
            )

        self.assertEqual(approval_path.read_bytes(), before)
        self.assertFalse(
            any(
                p.name.startswith("0001-")
                for p in (self.root / ".intent" / "events").glob("*.yaml")
            )
        )

    def test_duplicate_scope_paths_are_canonicalized(self):
        event = _validate_event_shape(
            {
                "v": 1,
                "seq": 0,
                "prev": None,
                "actor": "agent",
                "ops": [
                    {
                        "type": "ADD",
                        "atom": atom(
                            "REQ-0001",
                            "feature.one",
                            scope=["src", "src"],
                        ),
                    }
                ],
                "note": "duplicate scope",
            }
        )
        self.assertEqual(
            event["ops"][0]["atom"]["scope"]["paths"],
            ["src"],
        )


if __name__ == "__main__":
    unittest.main()
