from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import yaml

from .benchmark import run_direct, run_matched_pair
from .intent_gate import ApprovalRequired, IntentGateError, append_event, approve_evidence, brief, build_candidate_event, converge, evidence_hash, preflight, resolve
from .kernel import run_supervised
from .task_source import LocalTaskSource


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ctfh2")
    sub = p.add_subparsers(dest="command", required=True)

    smoke = sub.add_parser("smoke", help="run the local native-agent vertical slice")
    smoke.add_argument("--fixture", default=str(Path(__file__).resolve().parents[2] / "benchmarks" / "fixtures" / "smoke"))
    smoke.add_argument(
        "--agent",
        choices=("codex", "claude"),
        default="codex",
        help="native agent adapter",
    )
    smoke.add_argument(
        "--model",
        help="native model; defaults to gpt-6-sol for Codex and claude-opus-5-5 for Claude",
    )
    smoke.add_argument("--effort", default="high", choices=("low", "medium", "high", "xhigh"))
    smoke.add_argument("--mode", choices=("direct", "supervised", "pair"), default="pair")
    smoke.add_argument(
        "--interactive-session",
        action="store_true",
        help="admit the WorkerEnvironment interactive PTY session service",
    )
    smoke.add_argument(
        "--browser",
        action="store_true",
        help="admit the WorkerEnvironment GhostChrome browser service",
    )
    smoke.add_argument(
        "--browser-domain",
        action="append",
        default=[],
        help="browser navigation allowlist domain; repeat for multiple domains",
    )
    smoke.add_argument(
        "--knowledge-snapshot",
        help="read-only frozen knowledge snapshot directory",
    )
    smoke.add_argument("--out", help="output root; default .ctfh-runs/smoke-<timestamp>")

    intent = sub.add_parser("intent", help="resolve and enforce the active project contract")
    intent.add_argument("--root", default=".", help="project root containing .intent/")
    isub = intent.add_subparsers(dest="intent_command", required=True)

    res = isub.add_parser("resolve", help="replay the ledger and print active state")
    res.add_argument("--write", action="store_true")
    res.add_argument("--brief", action="store_true")

    br = isub.add_parser("brief", help="render compact task-facing intent brief")
    br.add_argument("--path", action="append", default=[])

    chk = isub.add_parser("check", help="run deterministic preflight checks")
    chk.add_argument("--ref", action="append", default=[])
    chk.add_argument("--path", action="append", default=[])
    chk.add_argument("--require-cache", action="store_true")

    app = isub.add_parser("append", help="append an accepted contract delta")
    app.add_argument("proposal", help="YAML proposal with base ledger_head and ops")
    app.add_argument("--actor", choices=("agent", "principal"), default="agent")
    app.add_argument("--approver", default=os.environ.get("USER", "project-principal"))

    ev = isub.add_parser(
        "approve-evidence",
        help="bind principal approval to the exact convergence evidence hash",
    )
    ev.add_argument("evidence", help="YAML evidence file")
    ev.add_argument(
        "--approver",
        default=os.environ.get("USER", "project-principal"),
    )

    conv = isub.add_parser("converge", help="evaluate applicable contract obligations")
    conv.add_argument("--evidence")
    conv.add_argument("--path", action="append", default=[])
    conv.add_argument("--no-command", action="store_true")
    conv.add_argument("--out")

    return p


async def _smoke(args: argparse.Namespace) -> dict[str, object]:
    fixture = Path(args.fixture).resolve()
    bundle = LocalTaskSource(fixture).load()
    task = bundle.spec
    root = Path(args.out).resolve() if args.out else Path.cwd() / ".ctfh-runs" / f"smoke-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    model = args.model or (
        "claude-opus-5-5" if args.agent == "claude" else "gpt-6-sol"
    )
    browser_domains = tuple(sorted(set(args.browser_domain)))
    if browser_domains and not args.browser:
        raise ValueError("--browser-domain requires --browser")
    knowledge_snapshot = (
        None
        if args.knowledge_snapshot is None
        else Path(args.knowledge_snapshot).resolve()
    )
    if args.mode == "direct":
        return await run_direct(
            task=task,
            run_root=root,
            model=model,
            reasoning_effort=args.effort,
            evaluator_data=bundle.evaluator_data,
            interactive_session_service=args.interactive_session,
            browser_service=args.browser,
            browser_allowed_domains=browser_domains,
            knowledge_snapshot_dir=knowledge_snapshot,
            agent=args.agent,
        )
    if args.mode == "supervised":
        return await run_supervised(
            task=task,
            run_root=root,
            model=model,
            reasoning_effort=args.effort,
            evaluator_data=bundle.evaluator_data,
            interactive_session_service=args.interactive_session,
            browser_service=args.browser,
            browser_allowed_domains=browser_domains,
            knowledge_snapshot_dir=knowledge_snapshot,
            agent=args.agent,
        )
    return await run_matched_pair(
        task=task,
        root=root,
        model=model,
        reasoning_effort=args.effort,
        evaluator_data=bundle.evaluator_data,
        interactive_session_service=args.interactive_session,
        browser_service=args.browser,
        browser_allowed_domains=browser_domains,
        knowledge_snapshot_dir=knowledge_snapshot,
        agent=args.agent,
    )


def _load_proposal(path: Path) -> dict[str, object]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise IntentGateError("proposal must be a YAML mapping")
    return data


def _safe_convergence_output(root: Path, value: str) -> Path:
    out = Path(value)
    out = (Path.cwd() / out).resolve() if not out.is_absolute() else out.resolve()
    project = root.resolve()
    try:
        out.relative_to(project)
    except ValueError:
        pass
    else:
        raise IntentGateError(
            "intent converge --out must be outside the project root in v0; "
            "writing inside the checked repository would invalidate the verified snapshot"
        )

    # A path outside the repository can still be a hard link to a tracked file
    # inside it. v0 avoids that aliasing class entirely: convergence reports are
    # write-once outputs and may never replace an existing filesystem object.
    if out.exists() or out.is_symlink():
        raise IntentGateError(
            "intent converge --out must name a new file; existing files, "
            "symlinks, and hard-link aliases are not overwritten"
        )
    return out


def _intent_main(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if args.intent_command == "resolve":
        result = resolve(root, write_cache=args.write)
        if args.brief:
            print(brief(root), end="")
        else:
            print(json.dumps({"ledger_head": result.ledger_head, "contract_hash": result.contract_hash, "event_count": result.event_count, "active_atoms": list(result.active_atoms)}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.intent_command == "brief":
        print(brief(root, paths=args.path), end="")
        return 0

    if args.intent_command == "check":
        report = preflight(root, refs=args.ref, paths=args.path, require_cache=args.require_cache)
        print(json.dumps(asdict(report), indent=2, sort_keys=True))
        return 0 if report.ok else 2

    if args.intent_command == "append":
        proposal_path = Path(args.proposal).resolve()
        proposal = _load_proposal(proposal_path)
        _, event_hash, needs_principal = build_candidate_event(root=root, proposal=proposal, actor=args.actor)
        if needs_principal:
            if args.actor != "principal":
                raise IntentGateError("principal-authority change requires principal actor")
            if not sys.stdin.isatty():
                raise IntentGateError("principal approval requires an interactive TTY; " f"event hash is {event_hash}")
            phrase = f"APPROVE {event_hash}"
            print("Principal-authority contract change.")
            print(f"Event hash: {event_hash}")
            if input(f"Type exactly '{phrase}' to approve: ") != phrase:
                raise IntentGateError("principal approval was not confirmed")
            path = append_event(
                root=root,
                proposal_path=proposal_path,
                actor="principal",
                approver=args.approver,
                confirmed=True,
                approved_event_hash=event_hash,
            )
        else:
            path = append_event(root=root, proposal_path=proposal_path, actor=args.actor)
        print(path)
        return 0

    if args.intent_command == "approve-evidence":
        evidence_path = Path(args.evidence).resolve()
        data = _load_proposal(evidence_path)
        digest = evidence_hash(data)
        if not sys.stdin.isatty():
            raise IntentGateError(
                "principal evidence approval requires an interactive TTY; "
                f"evidence hash is {digest}"
            )
        phrase = f"APPROVE-EVIDENCE {digest}"
        print("Principal manual verification evidence.")
        print(f"Evidence hash: {digest}")
        if input(f"Type exactly '{phrase}' to approve: ") != phrase:
            raise IntentGateError("principal evidence approval was not confirmed")
        path = approve_evidence(
            root,
            evidence_path=evidence_path,
            approver=args.approver,
            approved_evidence_hash=digest,
        )
        print(path)
        return 0

    if args.intent_command == "converge":
        report = converge(root, evidence_path=None if args.evidence is None else Path(args.evidence).resolve(), paths=args.path, execute_commands=not args.no_command)
        payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.out:
            out = _safe_convergence_output(root, args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open("x", encoding="utf-8") as handle:
                handle.write(payload)
        print(payload, end="")
        return 0 if report["status"] == "PASS" else 2

    raise AssertionError(args.intent_command)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "smoke":
            result = asyncio.run(_smoke(args))
            print(json.dumps(result, indent=2, sort_keys=True))
            verdict = result["supervised"]["verdict"] if args.mode == "pair" else result["verdict"]
            return 0 if verdict["passed"] else 2
        if args.command == "intent":
            return _intent_main(args)
        return 1
    except (IntentGateError, ApprovalRequired, OSError, ValueError) as exc:
        print(f"ctfh2: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
