from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tomllib
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import yaml

from .benchmark import run_direct, run_matched_pair
from .intent_gate import ApprovalRequired, IntentGateError, append_event, approve_evidence, brief, build_candidate_event, converge, evidence_hash, preflight, resolve
from .kernel import run_supervised
from .live import run_live_contest
from .live_agent import CodexRunError
from .live_store import LiveStore
from .live_browser import capture_browser
from .live_config import native_launch
from . import native as ctfh_native
from .task_source import LocalTaskSource


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ctfh2", description="사이트만 입력해 시작: ctfh2 https://사이트주소 · 마지막 대회 재개: ctfh2",
    )
    sub = p.add_subparsers(dest="command", required=True)

    go = sub.add_parser("go", help="(기본) 문제 수집 → 문제마다 codex tmux 창 → 검사기가 flag·writeup 확인")
    go.add_argument("site", nargs="?", help="CTF 사이트 URL; 생략하면 현재/마지막 대회 폴더")
    go.add_argument("--dir", help="대회 폴더 (기본: ~/Desktop/CTF/<사이트>)")
    go.add_argument("--no-attach", action="store_true", help="tmux 세션을 만들기만 하고 붙지 않음")
    _native_options(go)
    checker = sub.add_parser("checker", help="검사기 루프 (보통 tmux checker 창에서 자동 실행)")
    checker.add_argument("dir")
    checker.add_argument("--once", action="store_true", help="한 번만 확인하고 종료")
    _native_options(checker)
    done = sub.add_parser("done", help="직접 푼 문제 표시: 그 문제의 codex 창을 닫음")
    done.add_argument("challenge", nargs="+")
    done.add_argument("--dir")
    approve = sub.add_parser("approve", help="검증을 건너뛰고 현재 flag.txt를 한 번 제출")
    approve.add_argument("challenge")
    approve.add_argument("--dir")
    status = sub.add_parser("status", help="대회 진행 상태")
    status.add_argument("--dir")
    secret = sub.add_parser("secret-fill", help="로그인 입력창을 credentials.toml 값으로 채움 (값은 출력하지 않음)")
    secret.add_argument("dir")
    secret.add_argument("field", choices=("username", "password"))
    secret.add_argument("ref")
    secret.add_argument("--session", required=True)

    start = sub.add_parser("start", help="(이전 방식) live TUI로 사이트 입력부터 풀이까지 실행")

    start.add_argument("site", nargs="?", help="사이트 URL; 생략하면 마지막 대회 재개")
    start.add_argument("--account", help="여러 계정을 구분할 때만 사용하는 이름")
    start.add_argument("--auth-entry", help="저장된 chrome-use 로그인 금고 항목")
    start.add_argument("--model", help="이번 대회에서 사용할 Codex 모델")
    start.add_argument("--thinking", "--reasoning-effort", dest="reasoning_effort",
                       choices=("minimal", "low", "medium", "high", "xhigh", "max", "ultra"),
                       help="Codex 추론 강도; 예: xhigh (생략하면 계정 설정 사용)")
    start.add_argument("--max-workers", type=int, help="필요할 때만 지정하는 동시 문제 수 제한")
    start.add_argument("--max-turns", type=int, help="필요할 때만 지정하는 전체 턴 수 제한")
    start.add_argument("--codex", choices=("codex", "codex-secondary", "codex --secondary"),
                       metavar="CODEX", help="codex 실행기 선택 (생략하면 ctfh.toml)")
    start.add_argument("--secondary", action="store_true", help="보조 계정으로 실행 (--codex codex --secondary)")
    start.add_argument("--plain", action="store_true", help="TUI 대신 줄 단위 진행 로그 표시")

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

    live = sub.add_parser("live", help="browser-driven continuous CTF contest")
    lsub = live.add_subparsers(dest="live_command", required=True)
    run = lsub.add_parser("run", help="start or resume a live contest")
    run.add_argument("site", nargs="?", help="CTF site URL; omit when resuming saved state")
    run.add_argument("--state", default=".ctfh-live/current", help="private contest state directory")
    run.add_argument("--account", help="non-secret account label")
    run.add_argument("--auth-entry", help="chrome-use auth vault entry name, never a password")
    run.add_argument("--model", help="Codex model; otherwise use the selected account's default")
    run.add_argument("--thinking", "--reasoning-effort", dest="reasoning_effort",
                     choices=("minimal", "low", "medium", "high", "xhigh", "max", "ultra"),
                     help="Codex reasoning effort; omitted to use the account default")
    run.add_argument(
        "--cyber-access-program",
        choices=("standard", "daybreak_blue", "daybreak_red"),
        help="explicit approved OpenAI cyber access tier; omitted by default",
    )
    run.add_argument("--codex", choices=("codex", "codex-secondary", "codex --secondary"),
                     metavar="CODEX", help="Codex account (default: ctfh.toml)")
    run.add_argument("--secondary", action="store_true", help="use the installed secondary Codex account wrapper")
    run.add_argument("--engine", choices=("codex",),
                     help="native runner choice; retained for existing codex commands")
    run.add_argument("--agent-command", help="user connection command as a JSON argv array; never evaluated by a shell")
    run.add_argument("--reset-thread", action="store_true", help="keep contest progress but start a new agent session")
    run.add_argument("--stop-when-solved", action="store_true", help="stop once every discovered challenge is solved")
    run.add_argument("--max-turns", type=int, help="finite turn count for an interactive check")
    run.add_argument("--max-workers", type=int, help="optional cap on simultaneous problem workers; default is all ready problems")
    run.add_argument("--scan-seconds", type=float, default=15, help="interval for checking site solves and new challenges")
    run.add_argument("--idle-seconds", type=float, default=60)
    run.add_argument("--turn-timeout", type=float, default=3600)
    run.add_argument(
        "--resume-handoff", action="store_true",
        help="resume after the user has completed an unavoidable browser login step",
    )

    live_status = lsub.add_parser("status", help="inspect saved contest progress")
    live_status.add_argument("--state", default=".ctfh-live/current")
    live_status.add_argument("--details", action="store_true", help="include saved challenge checkpoints")

    events = lsub.add_parser("events", help="show public AI activity and tool lifecycle events")
    events.add_argument("--state", default=".ctfh-live/current")
    events.add_argument("--follow", "-f", action="store_true", help="keep displaying new events; Ctrl-C closes the viewer only")
    events.add_argument("--challenge", help="filter by a challenge key from status --details")
    events.add_argument("--limit", type=int, default=50, help="initial recent events (0 to 5000)")
    events.add_argument("--json", action="store_true", help="print one JSON object per event")

    engine_check = lsub.add_parser("check-engine", help="check selected Codex login without starting a model turn")
    engine_check.add_argument("--codex", choices=("codex", "codex-secondary", "codex --secondary"),
                              metavar="CODEX", help="Codex account (default: ctfh.toml)")
    engine_check.add_argument("--secondary", action="store_true", help="use the installed secondary Codex account wrapper")

    init = lsub.add_parser("init", help="prepare contest state without connecting a model")
    init.add_argument("site")
    init.add_argument("--state", default=".ctfh-live/current")
    init.add_argument("--account")
    init.add_argument("--auth-entry")
    init.add_argument("--model")
    init.add_argument("--thinking", "--reasoning-effort", dest="reasoning_effort",
                      choices=("minimal", "low", "medium", "high", "xhigh", "max", "ultra"))

    browser = lsub.add_parser("browser", help="chrome-use with turn-bound browser receipts")
    browser.add_argument("--state", default=".ctfh-live/current")
    browser.add_argument("--turn", type=int, required=True)
    browser.add_argument("browser_args", nargs=argparse.REMAINDER)

    submission = lsub.add_parser(
        "submission-begin", help="journal a candidate before browser submission"
    )
    submission.add_argument("--state", default=".ctfh-live/current")
    submission.add_argument("--challenge", required=True)
    submission.add_argument("--sha256", required=True)
    submission.add_argument("--turn", type=int, help="require this active controller turn")

    solved = lsub.add_parser("mark-solved", help="record problems you solved and stop their workers")
    solved.add_argument("--state", default=".ctfh-live/current")
    solved.add_argument("--challenge", required=True, action="append",
                        help="stable challenge key; repeat for several problems")

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


def _selected_codex(args) -> str | None:
    value = args.codex
    if args.secondary:
        if value not in (None, "codex", "codex-secondary", "codex --secondary"):
            raise ValueError("--secondary는 codex 실행기와 함께 사용하세요.")
        return "codex-secondary"
    return "codex-secondary" if value == "codex --secondary" else value


def _native_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--codex", choices=("codex", "codex-secondary", "codex --secondary"), metavar="CODEX",
                        help="codex 실행기 (기본: ctfh.toml [native] binary)")
    parser.add_argument("--secondary", action="store_true", help="codex-secondary 계정으로 실행")
    parser.add_argument("--model", help=f"Codex 모델 (기본 {ctfh_native.DEFAULT_MODEL})")
    parser.add_argument("--thinking", "--reasoning-effort", dest="reasoning_effort", choices=ctfh_native.EFFORTS,
                        help="Codex 추론 강도 (생략하면 계정 기본값)")
    parser.add_argument("--max-parallel", type=int, help="동시에 여는 풀이 창 수 (기본 0: 전체 문제)")
    parser.add_argument("--no-submit", action="store_true", help="flag 자동 제출 끔 (검증·writeup 검토만)")
    parser.add_argument("--plain", action="store_true", help="검사기 화면을 키 조작 없는 텍스트 표로 표시")
    parser.add_argument("--rescan-model",
                        help=f"주기적 재스캔에만 쓸 모델 (same이면 --model과 같음, 기본 {ctfh_native.RESCAN_MODEL})")
    parser.add_argument("--rescan-thinking", choices=(*ctfh_native.EFFORTS, "same"),
                        help="주기적 재스캔에만 쓸 추론 강도 (same이면 --thinking과 같음, 기본 low)")
    parser.add_argument("--skip-category", help="쉼표로 구분한 분야는 자동 풀이를 시작하지 않고 hold로 둔다 "
                        "(이름 일부만 맞아도 됨, 예: osint,misc; r로 직접 시작)")
    parser.add_argument("--submit-thinking", choices=(*ctfh_native.EFFORTS, "same"),
                        help="사이트 제출 작업의 추론 강도 (same이면 --thinking과 같음, 기본 low)")
    parser.add_argument("--review-thinking", choices=(*ctfh_native.EFFORTS, "same"),
                        help="writeup 검토 작업의 추론 강도 (same이면 --thinking과 같음, 기본 medium)")
    parser.add_argument("--rescan-minutes", type=int, help="새 문제·해결 상태 재수집 주기 (0이면 끔, 기본 60)")
    parser.add_argument("--idle-seconds", type=int, help="멈춘 풀이 세션을 깨우기까지 기다리는 시간 (기본 180)")
    parser.add_argument("--nudge-limit", type=int, help="문제당 자동 재촉·재시작 최대 횟수, 넘으면 manual 상태로 사용자에게 넘김 (기본 3)")
    parser.add_argument("--solve-timeout", type=int, help="solve.py 재실행 제한 시간 초 (기본 300)")
    parser.add_argument("--memory-max", help="풀이 창 전체가 함께 쓸 메모리 상한: auto(RAM의 98%%), 28G 같은 크기, off (기본 auto)")


def _native_settings(args) -> ctfh_native.Options:
    root = Path(__file__).resolve().parents[2]
    path = root / "ctfh.toml"
    config = tomllib.loads(path.read_text()).get("native", {}) if path.is_file() else {}
    binary = _selected_codex(args) or native_launch(root).binary
    pick = lambda name, key, default: (getattr(args, name, None) if getattr(args, name, None) is not None
                                       else config.get(key, default))
    rescan = pick("rescan_minutes", "rescan_minutes", 60)
    try:
        ctfh_native.memory_limit(str(pick("memory_max", "memory_max", "auto")))
    except ValueError as error:
        raise SystemExit(f"ctfh2: {error}")
    skip = pick("skip_category", "skip_categories", "")
    skip = ",".join(map(str, skip)) if isinstance(skip, list) else str(skip)
    return ctfh_native.Options(
        binary=binary, model=pick("model", "model", ctfh_native.DEFAULT_MODEL), effort=pick("reasoning_effort", "thinking", None),
        rescan_model=None if (rescan_model := pick("rescan_model", "rescan_model", ctfh_native.RESCAN_MODEL)) == "same" else rescan_model,
        rescan_effort=None if (rescan_effort := pick("rescan_thinking", "rescan_thinking", "low")) == "same" else rescan_effort,
        skip_categories=tuple(part.strip().lower() for part in skip.split(",") if part.strip()),
        submit_effort=None if (submit_effort := pick("submit_thinking", "submit_thinking", "low")) == "same" else submit_effort,
        review_effort=None if (review_effort := pick("review_thinking", "review_thinking", "medium")) == "same" else review_effort,
        max_parallel=max(0, int(pick("max_parallel", "max_parallel", 0))),
        submit=not args.no_submit and bool(config.get("submit", True)),
        plain=args.plain or bool(config.get("plain", False)),
        rescan_seconds=max(0, int(rescan)) * 60, idle_seconds=int(pick("idle_seconds", "idle_seconds", 180)),
        nudge_limit=int(pick("nudge_limit", "nudge_limit", 3)),
        solve_timeout=int(pick("solve_timeout", "solve_timeout", 300)),
        memory_max=str(pick("memory_max", "memory_max", "auto")),
    )


def _native_main(args) -> int:
    if args.command == "go":
        opts = _native_settings(args)
        directory = args.dir or (None if args.site else ctfh_native.find_root(None, required=False))
        return ctfh_native.go(args.site, str(directory) if directory else None, opts, attach=not args.no_attach)
    if args.command == "checker":
        contest = ctfh_native.Contest(Path(args.dir))
        checker = ctfh_native.Checker(contest, _native_settings(args), ctfh_native.Tmux(ctfh_native.session_name(contest.root)))
        if args.once:
            print(checker.tick(), end="")
            checker.pool.shutdown(wait=True)
            return 0
        if not checker.opts.plain and sys.stdin.isatty() and sys.stdout.isatty():
            from .native_tui import run_dashboard
            run_dashboard(checker)
            return 0
        try:
            checker.run()
        except KeyboardInterrupt:
            return 0
    if args.command == "done":
        root = ctfh_native.find_root(args.dir)
        print("직접 해결로 표시:", ", ".join(ctfh_native.mark_done(root, args.challenge)))
        return 0
    if args.command == "approve":
        status = ctfh_native.approve(ctfh_native.find_root(args.dir), args.challenge)
        print(f"{args.challenge}: {status['state']} ({status['reason']})")
        return 0
    if args.command == "status":
        root = ctfh_native.find_root(args.dir)
        board = root / ".ctfh" / "board.txt"
        print(board.read_text() if board.is_file() else ctfh_native.status_board(root))
        return 0
    if args.command == "secret-fill":
        ctfh_native.secret_fill(Path(args.dir), args.field, args.ref, args.session)
        print(f"{args.field} 입력 완료")
        return 0
    return 1


NATIVE_COMMANDS = {"go", "checker", "done", "approve", "status", "secret-fill"}


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if not values or values[0].startswith(("https://", "http://")) \
            or (values[0].startswith("--") and values[0] != "--help"):
        values.insert(0, "go")
    args = build_parser().parse_args(values)
    try:
        if args.command in NATIVE_COMMANDS:
            return _native_main(args)
        if args.command == "start":
            from .live_start import start_contest
            try:
                root = Path(__file__).resolve().parents[2]
                if sys.stdin.isatty() and sys.stdout.isatty() and not args.plain:
                    try:
                        from .live_tui import run_terminal_dashboard
                    except ModuleNotFoundError as exc:
                        if exc.name in {"textual", "rich"}:
                            raise ValueError("TUI 의존성이 없습니다. pip install -e . 또는 --plain을 사용하세요.") from exc
                        raise
                    site = args.site
                    if site is None:
                        from .live_start import last_state
                        if last_state(root / ".ctfh-live") is None:
                            site = input("CTF 사이트 주소: ").strip()
                    result = asyncio.run(run_terminal_dashboard(
                        root=root, site=site, account=args.account, auth_entry=args.auth_entry,
                        max_workers=args.max_workers, max_turns=args.max_turns,
                        codex_binary=_selected_codex(args),
                        model=args.model, reasoning_effort=args.reasoning_effort,
                    ))
                else:
                    result = asyncio.run(start_contest(
                        root=root, site=args.site, account=args.account, auth_entry=args.auth_entry,
                        max_workers=args.max_workers, max_turns=args.max_turns,
                        codex_binary=_selected_codex(args),
                        model=args.model, reasoning_effort=args.reasoning_effort,
                    ))
            except KeyboardInterrupt:
                print("\n중단했습니다. ./ctfh2 로 이어갈 수 있습니다.")
                return 130
            return 0 if result["state"] != "needs_human" else 3
        if args.command == "smoke":
            result = asyncio.run(_smoke(args))
            print(json.dumps(result, indent=2, sort_keys=True))
            verdict = result["supervised"]["verdict"] if args.mode == "pair" else result["verdict"]
            return 0 if verdict["passed"] else 2
        if args.command == "intent":
            return _intent_main(args)
        if args.command == "live":
            if args.live_command == "check-engine":
                from .live_config import codex_environment, native_launch
                import shutil
                import subprocess
                root = Path(__file__).resolve().parents[2]
                native = native_launch(root, _selected_codex(args))
                if shutil.which(native.binary) is None:
                    raise ValueError(f"{native.command} 실행 파일을 찾지 못했습니다.")
                checked = subprocess.run([native.binary, "login", "status"],
                                         capture_output=True, text=True, timeout=10,
                                         env=codex_environment(native.binary))
                if checked.returncode:
                    raise ValueError(f"{native.command} 로그인 상태를 확인할 수 없습니다.")
                print(json.dumps({"engine": "codex", "command": native.command,
                                  "status": "ready", "model_turn_started": False}, ensure_ascii=False, indent=2))
                return 0
            if args.live_command == "run":
                from .live_config import native_launch
                native = native_launch(Path(__file__).resolve().parents[2], _selected_codex(args))
                agent_command = None
                if args.agent_command is not None:
                    value = json.loads(args.agent_command)
                    if not isinstance(value, list) or not value or not all(isinstance(arg, str) and arg for arg in value):
                        raise ValueError("--agent-command must be a nonempty JSON argv array")
                    agent_command = tuple(value)
                result = asyncio.run(run_live_contest(
                    state_dir=Path(args.state), site_url=args.site,
                    account_label=args.account, auth_entry=args.auth_entry,
                    model=args.model, reasoning_effort=args.reasoning_effort,
                    cyber_access_program=args.cyber_access_program,
                    codex_binary=native.binary, full_access=native.full_access,
                    max_turns=args.max_turns, idle_seconds=args.idle_seconds,
                    turn_timeout=args.turn_timeout, resume_handoff=args.resume_handoff,
                    agent_command=agent_command, reset_thread=args.reset_thread,
                    stop_when_solved=args.stop_when_solved,
                    max_workers=args.max_workers, scan_seconds=args.scan_seconds,
                    engine=args.engine,
                ))
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
                return 0 if result["state"] != "needs_human" else 3
            if args.live_command == "browser":
                browser_args = args.browser_args
                if browser_args[:1] == ["--"]:
                    browser_args = browser_args[1:]
                print(capture_browser(Path(args.state), args.turn, browser_args), end="")
                return 0
            if args.live_command == "init":
                from .live_store import LiveRunLock
                with LiveRunLock(Path(args.state)), LiveStore(Path(args.state)) as store:
                    store.ensure_contest(args.site, args.account, args.auth_entry,
                                         model=args.model, reasoning_effort=args.reasoning_effort)
                    print(json.dumps(store.summary(), ensure_ascii=False, indent=2))
                return 0
            with LiveStore(Path(args.state), create=False) as store:
                if args.live_command == "events":
                    from .live_events import event_lines
                    try:
                        for line in event_lines(store, limit=args.limit, challenge=args.challenge,
                                                follow=args.follow, as_json=args.json):
                            print(line, flush=True)
                    except KeyboardInterrupt:
                        pass
                    return 0
                if args.live_command == "status":
                    print(json.dumps(store.snapshot() if args.details else store.summary(), ensure_ascii=False, indent=2, sort_keys=True))
                    return 0
                if args.live_command == "submission-begin":
                    verdict = store.begin_submission(args.challenge, args.sha256,
                                                     turn_id=args.turn)
                    print(json.dumps({"submission": verdict}, sort_keys=True))
                    return 0
                if args.live_command == "mark-solved":
                    unknown = [key for key in args.challenge if store.conn.execute(
                        "SELECT 1 FROM challenges WHERE challenge_key=?", (key,)
                    ).fetchone() is None]
                    if unknown:
                        raise ValueError("unknown challenge key: " + ", ".join(unknown))
                    changed = {}
                    for key in args.challenge:
                        changed[key] = store.mark_solved(key)
                    print(json.dumps({"changed": changed, "summary": store.summary()},
                                     ensure_ascii=False, indent=2, sort_keys=True))
                    return 0
        return 1
    except (IntentGateError, ApprovalRequired, CodexRunError, OSError, ValueError, RuntimeError) as exc:
        print(f"ctfh2: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
