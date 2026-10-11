# CTFH V3 Final Implementation Review Status

Date: 2026-10-06

## Scope

This note records the final implementation-level review/hardening work performed after the V3 architecture review.

The architecture remains the thin-runtime design:

- Kernel / TaskSource / TargetProvider
- WorkerEnvironment-owned interactive session and browser capabilities
- AgentAdapter preserving native Codex/Claude loops
- OutcomeAuthority as the only success authority
- Artifact/Trace storage outside the ordinary Kernel/SQLite hot path
- content-addressed, read-only skills and frozen knowledge
- fully offline ctfh_learning promotion plane

## Reviewer status

### GPT-6.1 Sol

A final read-only implementation review was launched against the current uncommitted code. The long review timed out before writing a final READY/NOT_READY file, but its review log produced concrete correctness findings and reproductions that were incorporated.

The reviewer explicitly reached the point of saying it had not found an additional commit blocker at that stage, then continued exploring shutdown edge cases.

### GPT-6 Astra

A final read-only implementation review was also launched. It likewise timed out before a final verdict file, but produced concrete correctness findings that were incorporated, including the final promotion-evaluation RunLock comparability gap.

### Claude Opus 5.5

Opus 5.5 completed the earlier architecture review that drove the V3 simplification.

A fresh final-implementation rerun could not be completed because the local Claude Code account currently reports an expired/revoked OAuth session and is not logged in. This is recorded as an external reviewer-availability limitation, not a passing review.

No READY verdict is claimed for a reviewer that did not actually produce one.

## Concrete reviewer findings incorporated

### Runtime / lifecycle

- A blocked PTY send could hold the session-RPC teardown path before process-group kill.
  - RPC accepts now stop first.
  - in-flight RPC work is cancelled/settled before the process-group snapshot.
  - process groups are then killed before capacity can be released.
  - regression coverage reproduces the blocked-send case.

- PTY in-memory buffering was bounded but the evidence transcript file could grow without bound.
  - on-disk PTY capture is now bounded by the configured transcript quota.
  - total observed bytes and captured bytes are distinct.
  - truncation and quota metadata are sealed into the trace manifest.

- Concurrent first-use browser RPCs could race the lazy GhostChrome runtime startup.
  - per-owner browser startup is serialized.
  - startup/controller cancellation explicitly kills process groups.
  - regression coverage verifies only one GhostChrome serve runtime is created.

- RPC writer cancellation during wait_closed() could leave a transport half-closed.
  - cancellation now aborts the transport before re-raising.
  - both drain-cancel and wait_closed-cancel paths have regression tests.

### Artifact / trace

- Attempt sealing previously had an in-memory BytesIO/read_bytes scaling path.
  - ArtifactStore now hashes/copies files incrementally.
  - directory sealing writes the deterministic tar to a temp file and streams its hash into CAS.

- service evidence is published after the attempt-owned processes are quiesced and before the environment tree is destroyed.

### Offline learning / WikiSkill-style promotion

- candidate provenance-bearing fields are rehashed and revalidated on load.
- inherited provenance from referenced wiki events and parent candidates is carried forward and checked.
- unresolved provenance references fail closed.
- terminal candidate decisions are serialized and cannot flip from reject to promote later.
- promotion/final evaluation task splits are persisted as immutable learning-root state.
- candidate evaluations are bound to the exact candidate content digest.
- each evaluation record now references a sealed RunLock file in addition to declaring its base hash.
- learning recomputes RunLock base/full hashes from the file.
- RunLock task identity and treatment are checked.
- the candidate treatment must actually pin the promoted knowledge/skill digest.
- baseline and candidate RunLock profiles are normalized by removing only the allowed candidate treatment, then compared for equality.
- frozen-knowledge RunLocks additionally bind the base prompt hash and fixed knowledge-guidance prompt policy.
- baseline knowledge evaluations must use isolated memory=off.
- candidate knowledge evaluations must use the exact read-only frozen snapshot and expected materializer.

## Latest automated evidence

Current latest full local suite:

- 112 tests passed
- Python compileall passed
- git diff --check passed

Latest learning-only verification after RunLock evidence/profile binding:

- 20 tests passed

Earlier real execution evidence before the current external Codex quota exhaustion includes:

- multiple Codex direct/supervised matched pairs with authoritative PASS and identical base hashes
- real Claude Opus 5.5 direct/supervised matched pair with authoritative PASS before its OAuth session later expired
- native Codex interactive-session tool probe
- native Codex managed GhostChrome browser probe
- Codex frozen-knowledge probe reading the pinned snapshot
- frozen-knowledge Codex direct/supervised matched pair
- supervised attempt CAS + trace-manifest CAS publication

A final real Codex pair was attempted after the latest RunLock prompt-policy changes. Both direct and supervised arms failed identically before doing work because the local Codex account reported its usage limit had been exhausted until 2026-10-10 13:39. Both arms still produced identical base hashes and the same external quota error. This run is therefore not treated as a product regression or as positive solve evidence.

## Current conclusion

The known concrete correctness findings from the final Sol/Astra implementation-review logs have been converted into code changes and regression tests. The local test suite is green.

Because the final Sol/Astra reviewer processes timed out before emitting formal verdict files, and fresh Opus 5.5 access is blocked by expired local authentication, this note intentionally does not fabricate unanimous model READY verdicts.

The remaining external validation limitation is model/account availability, not a known failing local invariant.

## Session update — 2026-10-08

The preceding implementation conclusion covers the V3 runtime baseline. It does
not cover the user's clarified product goal: supply a site URL and account access
at run time, then have the configured native Codex CLI open the browser, log in,
discover and solve challenges, submit flags, check results, and keep going.

The user rejected building a site-specific connector as the prerequisite. Record
and resume browser-driven work using the existing intent-gate, native Codex
sessions, MCP/token optimization tools, GhostChrome, and chrome-use setup.
See `docs/DECISIONS.md`, "Current product direction — user clarification,
2026-10-08", for the current direction.

- DONE: inspected current entry points and confirmed the available solver entry
  is a local single-task smoke flow, not a continuous website workflow.
- DONE: confirmed `CodexCLIAdapter.materialized_config_text()` disables native
  memory/hooks/plugins, the host MCP configuration is not inherited, and
  `CodexCLIAdapter.command()` uses `--ephemeral`.
- DONE: `rtk pytest -q -o pythonpath=src` passed with **113 tests** during this
  session's status inspection. Plain `rtk pytest -q` first failed collection
  because the source package path was not set; the successful command above
  supplies that path. This is local baseline evidence only.
- IN_PROGRESS: alignment with the browser-driven automatic solver objective;
  the clarified direction and rejected connector approach are recorded.
- NOT_RUN: an implementation or end-to-end run of the native-tool-preserving,
  resumable browser login / discovery / solve / submit / continue workflow.
  No site login or flag submission was attempted in this session.
- No `STATE.md` or `.planning/` state file exists; this existing status document
  is the session record. The legacy `.intent/` ledger was not amended.

## Research update — 2026-10-08

- DONE: searched CTF, browser-agent, native Codex, context, and prompt-injection
  research with Exa; directly opened 30 source pages and checked selected
  GitHub implementation files at pinned commits. Findings and the proposed
  live-contest architecture are in
  `docs/research/BROWSER_NATIVE_CTF_SOLVER_2026-10-08.md`.
- DONE: incorporated the latest user browser instruction: chrome-use and its
  connected Chrome profile are the live-workflow priority; the built-in Codex
  chrome plugin is only the final compatibility fallback. Earlier GhostChrome
  decisions remain historical context for the isolated benchmark worker.
- DONE: `rtk git diff --check` completed with no whitespace errors after the
  research-document update.
- NOT_RUN: implementation, site login, challenge solving, flag submission, or
  live browser capability probing. The research note is a design proposal.

## Live solver implementation update — 2026-10-08

- DONE: added the separate `ctfh2 live` command, a private SQLite contest and
  submission journal, a single-controller lock, persisted Codex thread ID,
  structured output, and `codex exec resume` without `--ephemeral` or stripped
  host MCP/plugin settings. The existing isolated benchmark path is unchanged.
- DONE: Codex's final claimed verdict must match a phrase in browser tool
  output from the same turn. A candidate must be journaled before a browser
  submission; uncertain submissions remain pending after interruption.
- DONE: `rtk pytest -q -o pythonpath=src` passed **122 tests**. The local
  browser fixture check `rtk proxy python3 tests/manual_live_browser.py`
  passed login, two correct submissions, attachment reading, and an alternate
  UI scoreboard. The disposable test auth entry was deleted and the fixture
  systemd unit was stopped and verified inactive.
- DONE: a short real Codex CLI probe produced `READY`, proving the installed
  account can execute an ordinary turn at this time.
- IN_PROGRESS: the first real Codex live turn against the local fixture
  obtained and saved a thread ID but was stopped before browser interaction
  by Codex's cyber safety gate. The CLI now classifies this error and records
  it without storing raw tool output. The default access program remains
  unchanged; an explicit option exists only for already approved accounts.
- NOT_RUN: a successful real Codex login/solve/submit/continue cycle, and any
  run on the user's actual CTF site. At this implementation checkpoint, the
  real CTF URL had not yet been supplied; see the later site inspection below.

## Actual site inspection — 2026-10-08

- DONE: the user supplied `https://h4ckingga.me/challenges`. The connected
  Chrome profile was already logged in. `chrome-use` displayed a CTFd challenge
  list, an unsolved challenge modal with a flag field and Submit button, and
  the site's Rules page. No credential was requested or copied.
- DONE: the Rules page forbids attacks on the H4CKING GAME main server and
  abnormal access. The live-agent prompt now limits work to published challenge
  targets/instances/files and instructs it to skip unclear scope.
- DONE: after the scope prompt edit, reran `rtk pytest -q -o pythonpath=src`
  with **122 passed** and `rtk git diff --check` with no whitespace errors.
- IN_PROGRESS: the real Codex automated turn remains blocked by its cyber
  safety gate, which rejected the local CTF fixture turn before browser use.
  The user does not know whether Daybreak access is approved. No Daybreak tier
  was selected and no real-site solve or flag submission was attempted.

## Native transport correctness update — 2026-10-08

- DONE: fixed browser evidence recognition for Codex's shell-wrapped command
  events. Compound shell commands and failed browser calls cannot supply
  verdict evidence. The official non-interactive documentation includes a
  `bash -lc` command event, matching the wrapper this implementation must handle.
- DONE: replaced the first-20-observations cutoff with a bounded rolling window
  of up to 256 observations / 4,000,000 characters so late verdicts remain visible.
- DONE: handle a final JSONL event without a newline, ignore non-object noise,
  consume an early rejection after stdin closes, and reject a `turn.failed`
  event even if the CLI exits with status zero. Failed runs now report contest
  state `failed`; missing binaries and timeouts have distinct error kinds.
- DONE: `rtk pytest -q -o pythonpath=src` passed **126 tests** after these fixes.
- FAILED: `tests/manual_native_transport.py` was intended to check two real
  native Codex processes reading only the official documentation tab title,
  including thread resume and recall of a random marker. The first service
  launch failed before Codex started because the systemd environment lacked
  its PATH. The second launch inherited PATH and CODEX_HOME but failed on the
  first turn with `native browser output was not recognized`. The real event
  path therefore still needs investigation; the fake-event tests do not prove
  native browser interoperability. The service exited with status 1 and
  MainPID 0; no test job remains running.
- NOT_RUN: a successful real Codex CTF solve/submit/continue cycle. The existing
  cyber safety rejection and unknown Daybreak approval remain unresolved.
- STOPPED: the user cleared the automatic goal. Further implementation and
  investigation stopped; no new CTF run or Daybreak selection was attempted.

## Contest agent delivery — user-owned Codex connection, 2026-10-08

The user resumed implementation with: "codex는 내가 연결할게 -> 일단 ctf agnet
완성해줘". This delivery completes the contest execution core and its public
connection contract. Actual Codex connection and model solve performance are
user-owned follow-up work, not acceptance gates for this core delivery.

- DONE: `live init` prepares a contest without starting any model. A persisted
  JSON argv connection uses `ctfh.turn.v1`; the core sends a prompt, report
  schema, current assignment, turn ID, and durable snapshot. Thread resume,
  explicit thread reset, failure, timeout, and login handoff are supported.
- DONE: challenge checkpoints, next actions, hashed workspace paths, attempts,
  cooldowns, pending-result priority, and fair selection survive restart.
  Current assignment is distinct from the next candidate. Status counts all
  challenges even when the model's list is limited to 100 entries.
- DONE: `live browser` executes chrome-use in the saved session and captures
  the actual URL/output as private, temporary per-turn receipts. Verdicts
  require matching same-origin, same-URL evidence captured after the candidate
  was journaled. Receipts are cleaned after success, failure, or recovery.
  Correctness no longer depends on Codex-specific browser event extraction.
- DONE: concurrent submission helpers serialize their checks; unresolved
  candidates block new candidates for that problem. Accepted/rejected outcomes
  and solved problems cannot be replayed. Candidate hashes, not raw flags, are
  kept in the submission journal.
- DONE: `rtk pytest -q -o pythonpath=src` passed **135 tests** on the final
  implementation. Tests cover the external connection, persisted sessions,
  handoff, timeout, concurrent submissions, scheduling, checkpoints, counts,
  stale/wrong-origin/wrong-URL evidence, and CLI initialization.
- DONE: actual Chrome/controller integration first passed using an injected
  fixture agent, then passed through a separate JSONL connection process and
  the public browser CLI helper. The final finite job was:
  `rtk proxy systemd-run --user --unit=ctfh-core-integration-20261008-v3 --setenv=PATH --property=WorkingDirectory=/home/yc54616/Desktop/ctfh-core --property=RuntimeMaxSec=600 /usr/bin/env python3 tests/manual_live_core.py --state .ctfh-live/core-integration-20261008-v3`.
  Result: two accepted problems, one rejected candidate, zero replayed
  submissions, successful recovery after a lost report following submission,
  and the alternate UI scoreboard confirmed 2/2. The job exited 0 with
  `ActiveState=inactive`, `SubState=dead`, `MainPID=0`; its fixture server and
  named browser session were closed.
- Evidence: `.ctfh-live/core-integration-20261008-v3/integration-result.json`
  (private, ignored), corresponding user-unit journal, and
  `tests/manual_live_core.py`. The first integration run found an assignment
  re-query mismatch and chrome-use's checkmark prefix on `get url`; both were
  fixed and included in the final regression checks.
- DONE: the connection and operating guide is `docs/LIVE_AGENT.md`.
- USER_OWNED / NOT_RUN: actual Codex connection, actual model solve performance,
  and autonomous solving on the user's external CTF site. This session made no
  external CTF submissions or model-access changes. The fixture connection is
  deterministic and localhost-only; its success is not a model benchmark.
- Scope reconciliation: legacy `.intent/` has no approved `INTENT.toml`; no
  intent-gate migration or authority-file edits were made. Existing unrelated
  worktree edits remain present. Delivery changes are local and uncommitted.

## Concurrent contest operation and user solves — 2026-10-08

The user corrected the single-problem schedule: run all problems at once,
accept user-entered solves, and stop only the corresponding worker. The user
confirmed both input paths: direct submission on the CTF site and explicit
solved-problem input to the agent.

- DONE: `live run` now starts an independent discovery/site-monitor turn and
  one worker for every discovered ready problem by default. Problem workers
  have separate native agent threads, named Chrome sessions, turn IDs, and
  workspaces. `--max-workers` is an optional user-controlled resource cap.
- DONE: `live mark-solved --challenge KEY` accepts one or more keys, marks
  them solved, interrupts only matching turns, and blocks stale browser and
  submission helpers. Pending candidate records remain factual and are
  removed from actionable queues without inventing an accepted verdict.
- DONE: the independent monitor checks the site every 15 seconds by default
  (`--scan-seconds` configurable). A browser-observed solved state records
  `solved_source=site` and interrupts its worker while others continue.
  Manual marking records `solved_source=user`.
- DONE: `rtk pytest -q -o pythonpath=src` passed **140 tests**. New tests
  check simultaneous worker starts, distinct threads/sessions, targeted
  cancellation, site receipt reconciliation, submission fencing, and pending
  candidate handling, and simultaneous startup of all 105 discovered problems
  beyond the 100-item prompt summary limit. `rtk git diff --check` passed.
- DONE: `rtk proxy python3 tests/manual_live_parallel.py --state
  .ctfh-live/parallel-integration-20261008-v1` exited 0. The private result
  file reports two concurrent workers, a direct site submission detected by
  the monitor, the other worker still active, and explicit manual completion.
  This used a deterministic local JSONL connection plus real Chrome; its
  dedicated sessions and localhost server were closed afterward.
- DONE: prior submission/restart regression also passed after this change:
  `rtk proxy python3 tests/manual_live_core.py --state
  .ctfh-live/core-integration-20261008-v4` exited 0 with accepted=2,
  rejected=1, replayed=0, and scoreboard 2/2.
- NOT_RUN: user-owned actual Codex connection and autonomous solving on the
  external CTF site. Neither local integration test is a model capability
  benchmark; no external CTF submission was performed.

## Checkout executable — 2026-10-08

- DONE: the executable `./ctfh2` uses this checkout's `src` directory and the
  existing CLI. Installation and an explicit `PYTHONPATH` are unnecessary.
- DONE: `rtk proxy ./ctfh2 live --help` exited 0. Running
  `rtk proxy /home/yc54616/Desktop/ctfh-core/ctfh2 live status --state
  /home/yc54616/Desktop/ctfh-core/.ctfh-live/parallel-integration-20261008-v1`
  from `/tmp` exited 0 and reported the saved two solved problems.
- DONE: `rtk git diff --check` passed. README and the operating guide include
  commands for starting, inspecting, and manually completing problems.
- NOT_RUN: a model connection or a new live contest solve; this change only
  adds a shorter entry point. Existing unrelated worktree edits remain local.

## Native codex-shift connection — 2026-10-08

User direction: use `codex-shift`; it is the same Codex. The user's
`계속해줘` authorizes this connection implementation. The earlier separation
between a finished contest core and a user-owned model connection remains
useful for reporting actual model validation, but no longer defers building
the requested shift connection.

- DONE: `live run --engine codex-shift` starts an owned native app-server,
  reuses the installed `codex_shift` model router/relay, and sends structured
  report turns through app-server JSON-RPC. The installed shift checkout and
  native Codex configuration were not modified. `websockets>=14,<16` is an
  optional `shift` dependency.
- DONE: engine choice survives restart. Native Codex engine switches retain
  native thread IDs; custom JSONL connection switches require thread reset.
  Discovery/monitor and problem threads remain independent. The shared shift
  runtime preserves model rotation across turns during one controller run;
  rotation counters reset when that runtime restarts.
- DONE: relay retries are disabled because CTFH owns attempt accounting and
  submission reconciliation. Model routing uses the installed shift defaults.
  Authentication, quota, rate-limit, policy, approval, and interactive-input
  failures stop the controller. No new permissions are granted.
- DONE: cancellation sends `turn/interrupt` for only the relevant thread/turn
  and waits for terminal confirmation. Runtime shutdown terminates/reaps its
  app-server process and removes its private temporary directory. Cleanup is
  safe when called after both startup failure and controller shutdown.
- DONE: `live check-engine` checks initialize/initialized through the relay
  without opening a thread or invoking a model. Missing optional dependencies
  produce an actionable CLI error. The new engine appears in status output.
- DONE: final focused command
  `rtk pytest -q -o pythonpath=src tests/test_live_shift.py tests/test_live_parallel.py tests/test_live_core.py tests/test_live.py`
  exited 0: **37 passed, 1 skipped**. Coverage includes the actual installed
  shift router/relay with in-memory WebSocket frames, model rotation, thread
  resume, controller selection/persistence, concurrent targeted cancellation,
  timeout interruption, denial handling, approval cancellation, a handshake
  without a model turn, missing dependencies, and real subprocess cleanup.
- DONE: `rtk proxy ./ctfh2 live run --help`,
  `rtk proxy ./ctfh2 live check-engine --help`, and
  `rtk git diff --check` exited 0. README, operating guide, and decisions
  describe the commands, dependencies, rotation lifetime, and verification limits.
- NOT_RUN: real Unix WebSocket transport test
  `test_runtime_starts_and_reaps_owned_app_server`. A direct Unix socket
  listener probe raises `PermissionError: [Errno 1] Operation not permitted`
  in this execution environment. The test explicitly skips on that condition;
  in-memory protocol tests are not evidence of successful real transport.
- IN_PROGRESS / UNVERIFIED: full current regression. The unrestricted earlier
  **140 passed** result predates shift integration. A current full-suite run
  was interrupted after stalling; a bounded 75-second run exited 124. A final
  diagnostic command,
  `rtk proxy timeout 45s python3 -m pytest -vv -o pythonpath=src -o faulthandler_timeout=20 > /tmp/ctfh-shift-regression.log 2>&1`,
  also exited 124, identifying
  `tests/test_v0.py::V0Tests::test_browser_controller_cancellation_barrier_kills_spawn_window_process`
  as the stalled test. All live tests reached before that point passed except
  the explicit socket skip. The underlying V0 stall has not been diagnosed;
  no full-suite pass is claimed for the current tree.
- NOT_RUN: real account/model turns, actual site login, and external CTF solves.
  No external CTF submission or model-access change was performed in this step.
  `rtk proxy ./ctfh2 live check-engine` remains the local transport check to run
  in an environment permitting Unix sockets.
- Scope reconciliation: existing unrelated dirty files were preserved. The
  session record is this existing status document; no new planning/intent
  system was initialized. Changes remain local and uncommitted.

## Public solver activity events — 2026-10-08

User request: `이벤트는 못보나 ai 가 어떻게 풀고 있는지`.
The prior implementation consumed native events but only exposed turn-end
summaries. This change makes public activity observable during the turn.

- DONE: `live events --state PATH --follow` prints recent activity and new
  events with time, problem key, and turn. `--challenge KEY`, `--limit N`,
  and `--json` support focused viewing and JSONL output. Ctrl-C exits only
  the viewer; it neither takes the controller lock nor cancels a worker.
- DONE: the private SQLite journal retains the latest 5,000 events across
  restarts. Problem/turn identity comes from the controller's assigned turn.
  Native exec, native shift, and custom JSONL progress use task-local sinks;
  concurrent callbacks cannot replace each other's destinations.
- DONE: visible events include public commentary, plan steps, tool lifecycle,
  checkpoints, submission registration/verdicts, solves, failures and worker
  stops. State transitions and their success events share the same transaction;
  an invalid report cannot leave a committed accepted-verdict event behind.
- DONE: reasoning, complete final reports, tool arguments, and raw tool output
  are excluded. Public prose is bounded, stripped of terminal control codes,
  and redacted for common secret formats. Arbitrary secret detection is not
  guaranteed. Events remain observational and cannot authorize submissions or
  establish verdict evidence. Earlier runs are not reconstructed retroactively.
- DONE: final command
  `rtk pytest -q -o pythonpath=src tests/test_live_events.py tests/test_live_shift.py tests/test_live_parallel.py tests/test_live_core.py tests/test_live.py`
  exited 0: **44 passed, 1 skipped**. New coverage verifies progress before
  turn completion, interleaved problem isolation, targeted cancellation,
  persisted history, filtered following without replay, bounded retention,
  redaction, report rollback, missing-state behavior, custom JSONL progress,
  native exec activity, and shift plan/tool/commentary notifications through
  the installed relay with a fake app-server.
- DONE: `rtk proxy ./ctfh2 live events --help` exited 0. A finite, temporary
  local fixture using `rtk proxy env PYTHONPATH=src python3` and the public CLI
  printed chronological start/message/tool-start/tool-completion lines.
  No browser or model was invoked; the fixture directory was removed.
  `rtk git diff --check` exited 0.
- NOT_RUN: real model activity and real Unix socket transport. The one skip
  remains the known environment restriction on Unix socket listeners. The
  previously stalled V0 full-suite test was not repeated because this change
  only touches the live workflow; no full-suite pass is claimed.
- Scope reconciliation: README, the operating guide, and Decision 17 document
  this request and behavior. Existing unrelated changes remain present and
  local. This existing status document remains the session record.

## Minimal-input contest startup — 2026-10-08

User correction: `최소한의 입력으로 자동화를 시켜서 지금 복잡해`.
Follow-up clarification: receive the problem list first, then run the problem
solvers. The existing all-ready-problem parallel behavior is preserved.

- DONE: `ctfh2 URL` starts the new single-screen flow. Bare `ctfh2` resumes
  the last contest or asks only for a site URL on first interactive use.
  Existing advanced CLI commands remain available.
- DONE: choose the private state path from site/account identity, reuse a
  matching existing contest (most recently updated when several match), and
  atomically store the last choice under this checkout's `.ctfh-live/last.json`.
  Stored engine choices and sessions remain intact. New contests prefer an
  installed shift launcher, otherwise native Codex. Failures do not cause
  automatic fallback to a different engine.
- DONE: shift startup now performs the handshake on its already-owned runtime
  before starting model turns. Users do not need a separate check command.
  Live events and numbered problem entries appear in the same terminal;
  verbose per-turn JSON summaries are disabled only in the simple flow.
- DONE: inline `목록`, `완료 번호`, and `종료` commands. A numbered solve marks
  only the selected challenge(s); an invalid batch makes no partial changes.
  Other workers keep running. Ctrl-C cancels the controller through its normal
  cleanup and leaves resumable state. Input uses cancellable fd notifications,
  without an executor thread waiting indefinitely for terminal input.
- DONE: a login handoff waits for a fresh Enter after the browser step. Old
  pending input is discarded before presenting the handoff prompt. A
  noninteractive run records handoff and returns without automatically
  reclaiming the browser. No passwords are requested or stored by the console.
- DONE: dependency compatibility repaired after a concrete regression. The
  installed `codex_shift` implementation had changed since the prior passing
  run: `_arguments([])` now gives `base_turns=None` with command-count switching,
  and its proxy resets routing on every thread/resume. This caused the existing
  model-rotation test to fail and would break runtime construction. The CTF
  adapter now explicitly uses supported `--base-turns 3` configuration and
  preserves router state, including denial holds, on routine reattachment.
  The installed shift checkout was only read, never modified.
- DONE: final command
  `rtk pytest -q -o pythonpath=src tests/test_live_start.py tests/test_live_events.py tests/test_live_shift.py tests/test_live_parallel.py tests/test_live_core.py tests/test_live.py`
  exited 0: **54 passed, 1 skipped**. New tests cover direct URL/no-argument CLI
  routing, first-run URL input, automatic selection, existing-state reuse,
  inline events, saved-thread resume, numbered targeted cancellation, login
  waiting/resume, noninteractive handoff, failure without fallback, invalid
  solve batches, and real pipe input cancellation/EOF. The repaired installed
  router integration again passes its five-turn rotation/resume test.
- DONE: `rtk proxy ./ctfh2 --help`, `rtk proxy ./ctfh2 start --help`, and
  `rtk git diff --check` exited 0. README and the operating guide now lead with
  the site-only workflow; Decision 18 records the user's correction.
- NOT_RUN: real model/site solving and real Unix socket transport. The one
  skip remains the execution environment's Unix socket restriction. Prior V0
  full-suite stall remains unverified; no full-suite pass is claimed.
- Scope reconciliation: no new planning/intent system or external service was
  initialized. All changes are local and uncommitted; pre-existing unrelated
  worktree changes remain present.

## Local contest dashboard — 2026-10-08

User authorization: after proposing a dashboard to reduce operating input,
the user said `가보자`. Build it over the existing live engine and records.

- DONE: `ctfh2 dashboard` command, loopback-only ASGI app and packaged Korean
  HTML/CSS/JavaScript UI. Site URL/start is the primary flow, with collapsed
  account/vault/concurrency options. Use the same contest preparation and
  engine selection as the terminal shortcut.
- DONE: full problem list, search/status filters, running/solved counts,
  progress, problem-scoped public events and checkpoints. Incremental polling
  keeps bounded history and clears it on contest changes. Untrusted strings
  use textContent and problem links admit only credential-free HTTP(S).
- DONE: start, targeted manual solve, pause and resume actions call the live
  controller. Completed problems stay excluded on resume; unfinished workers
  retain native thread IDs. A login handoff requires explicit `login_done`.
  Browser refresh leaves owned work running; server restart reads saved state
  but starts no model until an explicit user action. Other CLI owners remain
  observable and accept manual solve input, but cannot be paused through this
  dashboard. Service shutdown awaits owned controller/worker cleanup.
- DONE: Host/Origin/loopback checks, per-process request token, stale-contest
  action rejection, bounded JSON bodies and a restrictive same-origin CSP.
  No HTTP input accepts a shell command or filesystem state path. No password
  field is provided; existing connected Chrome/vault behavior is retained.
- DONE: launcher reuses a healthy server for this checkout, otherwise checks
  local socket permission before creating a detached systemd user service.
  Chrome opens via a named chrome-use session. `--port` and `--no-open` are
  supported, and service shutdown instructions are printed. Denied socket
  access cannot dispatch an out-of-context service as a workaround.
- DONE: final Python regression command
  `rtk pytest -q -o pythonpath=src tests/test_live_dashboard.py tests/test_live_start.py tests/test_live_events.py tests/test_live_shift.py tests/test_live_parallel.py tests/test_live_core.py tests/test_live.py`
  exited 0: **66 passed, 1 skipped**. The 12 dashboard cases use an actual ASGI
  HTTP transport and real SQLite/controller with fake model workers, plus
  mocked service/browser dispatch. They cover parallel execution, one-worker
  cancellation, refresh/restore, saved-thread resume, fresh login handoff,
  105-problem visibility, event cursors, external ownership, request rejection,
  runner failure, shutdown cleanup, service reuse and denied-port behavior.
- DONE: `rtk test node --test tests/test_dashboard_model.mjs` exited 0:
  **4 passed** (event merge/retention/session reset, filters, safe links, and
  start-vs-resume choice). `rtk proxy node --check src/ctfh_core/dashboard/app.js`
  and `rtk proxy ./ctfh2 dashboard --help` exited 0. CSS was parsed/formatted
  locally. A temporary static WeasyPrint rendering was inspected; it does not
  execute JavaScript and is not browser acceptance evidence.
- DONE: README, the live operating guide and Decision 19 describe the command,
  workflow, background-service lifetime and verification boundary. Current
  files and `rtk git status --short` were reconciled; pre-existing unrelated
  changes remain intact. `rtk git diff --check` exited 0.
- BLOCKED (runtime environment): actual
  `rtk proxy ./ctfh2 dashboard --no-open` exited 2 with
  `로컬 대시보드 연결을 확인할 수 없습니다: [Errno 1] Operation not permitted`.
  No server/service or model was launched by that command.
- NOT_RUN: real HTTP listener/systemd readiness, interactive desktop/mobile
  Chrome validation, and real account/site solving. The one Python skip remains
  the environment's Unix-socket listener restriction. No new full-repository
  test pass is claimed; the earlier V0 suite stall remains outside this change.
- Scope reconciliation: no external site, credential store, installed shift
  checkout or browser installation was changed. Work remains local and
  uncommitted; `.git` is read-only in this execution environment.

## Dashboard Not Found startup fix — 2026-10-08

User reported `./ctfh2 dashboard` failing with
`ctfh2: 로컬 대시보드 연결을 확인할 수 없습니다: Not Found`.

- DONE: reproduced the same failure without network access by passing a real
  urllib HTTPError(404, Not Found) through the launcher health check. The
  previous generic URLError handler treated an occupied/incompatible endpoint
  as a fatal connection failure. Two new regression cases initially failed.
- DONE: distinguish HTTP errors, invalid JSON/identity, and EADDRINUSE from
  connection/permission failures. Default startup searches 8787–8796, preferring
  a healthy dashboard for this checkout before selecting a free port. The
  selected port is used consistently for service identity, server arguments,
  readiness, displayed URL and Chrome navigation. Explicit `--port` stays fixed
  and now reports the port and HTTP status on conflict. Health probes never
  follow redirects; existing foreign services are not modified or stopped.
- DONE: `rtk pytest -q -o pythonpath=src tests/test_live_dashboard.py` exited 0:
  **16 passed**. New tests cover the reported 404 through actual urllib error
  handling, automatic selection and matching browser URL, explicit-port error,
  existing alternate-port service reuse, and fatal permission failure without
  probing other ports or dispatching a service.
- DONE: `rtk proxy ./ctfh2 dashboard --help` shows automatic port selection.
  README, live operating guide and Decision 19 were reconciled with this change.
- BLOCKED (runtime environment):
  `rtk proxy ./ctfh2 dashboard --no-open` still exits 2 with local connection
  `[Errno 1] Operation not permitted`. `ss` cannot open its netlink socket here,
  so the process answering the user's port 8787 cannot be identified from this
  environment. No service or browser was launched; no permission workaround
  was attempted.
- NOT_RUN: live server/Chrome verification on the user's host. Socket-free
  regression coverage does not establish a real model or site-solving result.
- Scope: launcher, CLI option, dashboard tests and related documentation only.
  Current files and git status were reconciled; unrelated local changes were
  preserved. `rtk git diff --check` exited 0. No commit was made.

## Secondary-account launch mode and approval handling — 2026-10-08

User reported `approval_required: Codex requires interactive input
(item/commandExecution/requestApproval)` with zero discovered problems.
Read-only inspection of the selected SQLite state confirmed that discovery
turn 1 failed under `codex-shift`, before a problem list was recorded. The
then-current RPC client cancelled native interactive requests and raised an
error. The original request's command preview was not retained.

During the fix the user clarified the intended launch:
`아 codex-secondary --dangerously-bypass-approvals-and-sandbox 이걸로 들어가는거야`.
This is the authoritative correction to the default-launch assumption.

- DONE: inspected the installed secondary wrapper read-only. It selects the
  secondary CODEX_HOME and credential store, shares the configured native
  history store, and removes API-key environment alternatives. No credential
  values were read or sent to external tools. Official app-server/CLI docs were
  fetched through Exa (Parallel unavailable) for approval and permission fields.
- DONE: checkout-local `ctfh.toml` explicitly selects `codex-secondary` and the
  user-requested full-access mode. Dashboard, terminal shortcut, `live run` and
  engine check read the profile. Other explicit binaries and absent profiles
  retain ordinary native defaults. Global config, the installed wrapper and
  the installed shift checkout were not edited.
- DONE: keep shift routing but launch its owned server through the secondary
  wrapper. Use process-scoped `-c approval_policy="never"` and
  `-c sandbox_mode="danger-full-access"`; pass equivalent explicit policies on
  thread/start, thread/resume and each turn/start, preserving native thread IDs.
  Native exec receives `--dangerously-bypass-approvals-and-sandbox` on both new
  and resumed runs. This config is for the user's solver process; it does not
  authorize bypassing the active coding environment's execution restrictions.
- DONE: fallback dashboard handling for native command/file approval requests
  under configurations that still emit them. Keep the RPC stream receiving
  while individual decisions wait; show request-specific command/cwd, file
  diff/root or network host/protocol. Only explicit individual decisions are
  accepted. Cross-thread, replayed, resolved, stopped and already-solved-worker
  requests cannot be accepted. Missing/truncated previews cannot be accepted.
  No automatic approval, policy amendment or session-wide acceptance exists.
  Raw review details are ephemeral and never enter the public event journal.
  Cancellation/turn completion/serverRequest-resolved clean up pending waiters.
- DONE: `dashboard --restart` stops only a healthy matching checkout/port unit,
  verifies it stopped, and launches the updated server. This lets the user
  apply updated code to the currently running dashboard while retaining the
  saved contest. The UI resumes only after the user's start/resume action.
- DONE: final Python command
  `rtk pytest -q -o pythonpath=src tests/test_live_config.py tests/test_live_dashboard.py tests/test_live_shift.py tests/test_live_start.py tests/test_live_events.py tests/test_live_parallel.py tests/test_live_core.py tests/test_live.py`
  exited 0: **87 passed, 1 skipped**. Includes profile/argv selection, explicit
  permission values through the installed shift relay on start/resume/every
  turn, unchanged default-policy behavior, HTTP approval decisions, isolated
  parallel approvals, stale-request/solved-worker rejection, pause/cleanup,
  restart dispatch and prior live workflow regressions. Model/protocol fixtures
  were used; no full-access model process was started by these tests.
- DONE: `rtk test node --test tests/test_dashboard_model.mjs` exited 0:
  **5 passed**; approval wait is treated as an active run for filtering and
  controls. `rtk proxy node --check src/ctfh_core/dashboard/app.js`, dashboard
  help, and live-run help exited 0. A read-only argv preview confirmed the
  actual checkout selects codex-secondary plus the intended process overrides;
  that preview starts no process. `rtk git diff --check` exited 0.
- DONE: README, the live operating guide and Decision 20 record the user's
  correction and supersede the previous implicit native-launch assumption.
  Existing status/history records remain intact. Worktree state was reconciled;
  pre-existing unrelated edits are preserved and no commit was made.
- NOT_RUN: actual account/model execution, local service restart and browser
  acceptance from this coding environment. The known local socket restriction
  still applies and the Unix-socket fixture is the one skip. No tool was used
  to escape that restriction, and no global runtime/security policy was edited.

## Native launcher argument and resume schema correction — 2026-10-08

Latest user request: `이게 codex-secondary 인지 codex 인지 정할 수 있으면 좋겠네 이건 인자로`.
The preceding application error was `rpc_failed: app-server rejected thread/resume`.

- DONE: added `--codex codex|codex-secondary` to the dashboard, terminal start,
  URL shortcut and last-contest resume. The explicit argument reaches the
  detached service and native controller without rewriting `ctfh.toml`.
  Omitting it keeps the project profile default. Selecting a different binary
  retains that launcher's native permission defaults; the configured secondary
  profile keeps the user's existing full-access opt-in.
- DONE: dashboard health/state and the UI expose the effective launcher. An
  explicit different launcher restarts only the identified checkout/port
  service; the same selection reuses it. Existing saved progress and thread
  IDs are retained. After a restart, the user explicitly resumes the contest.
- DONE: corrected an implementation mistake in the prior session. Thread
  start/resume sent `sandbox: dangerFullAccess`, but the installed Codex schema
  requires `sandbox: danger-full-access`. Turn start separately requires
  `sandboxPolicy.type: dangerFullAccess`. The previous mock accepted invalid
  values, so its passing tests did not establish native schema compatibility.
- DONE: generated schemas with
  `rtk proxy codex-secondary app-server generate-json-schema --out /tmp/ctfh-appserver-schema-20261008`
  (exit 0; no model turn or app-server listener). Added strict fixture rejection
  and a test comparing emitted start/resume/turn values against schemas freshly
  generated by the installed Codex. Before the fix,
  `rtk pytest -q -o pythonpath=src tests/test_live_shift.py -k 'explicit_full_access or wire_values'`
  reproduced the defect with **2 failed**. Both cases pass after correction.
- DONE: RPC errors now retain a bounded, redacted native reason and numeric
  code, with raw error data/request arguments excluded. A test verifies token
  redaction while preserving the useful invalid-variant explanation.
- DONE: final regression command
  `rtk pytest -q -o pythonpath=src tests/test_live_config.py tests/test_live_dashboard.py tests/test_live_shift.py tests/test_live_start.py tests/test_live_events.py tests/test_live_parallel.py tests/test_live_core.py tests/test_live.py`
  exited 0: **96 passed, 1 skipped**. The skip is the existing Unix-socket
  fixture under this environment's restriction. Coverage includes CLI routing,
  service argument propagation/reuse/restart, effective controller selection,
  installed native schema validation and the prior live workflow cases.
- DONE: `rtk test node --test tests/test_dashboard_model.mjs`,
  `rtk proxy node --check src/ctfh_core/dashboard/app.js`,
  `rtk proxy ./ctfh2 dashboard --help`, `rtk proxy ./ctfh2 start --help`
  and `rtk git diff --check` exited 0. README, the operating guide and Decision
  21 document the selector, permission defaults and protocol correction.
- DONE: reconciled this session against `rtk git status --short`. Existing
  unrelated tracked changes and prior live-agent files are preserved; no
  commit was made.
- NOT_RUN: actual local systemd restart, browser acceptance and account/model
  execution. Socket restrictions still prevent those checks in this coding
  environment. No credential values, global settings or saved contest state
  were changed, and no full-access model process was launched by these tests.

## Codex account selector wording — 2026-10-08

User correction: `codex로 붙거나, codex --secondary 로 수정해줘`; user then said
`계속해줘`. The preceding selector had exposed the wrapper name instead of the
requested command shape.

- DONE: `ctfh2 dashboard`, `start`, URL shortcuts, last-contest resume, and
  advanced `live run`/`check-engine` accept `--codex codex --secondary`.
  `--secondary` alone selects the same secondary account; the old
  `--codex codex-secondary` spelling remains accepted for compatibility.
  Selection stays an argument and does not rewrite `ctfh.toml`.
- DONE: verified the installed upstream CLI with
  `rtk proxy codex --secondary --help`: exit 2, unexpected argument. The flag
  therefore belongs to CTFH. It resolves to the installed `codex-secondary`
  wrapper; upstream Codex receives only supported arguments. The detached
  systemd command forwards `--codex codex --secondary`. The dashboard and
  terminal show the requested command shape while API state also retains the
  actual binary. Existing shift engine routing and saved threads are preserved.
- DONE: the dashboard launcher now compares the running service with the
  effective project default even when `--codex` is omitted. Returning from an
  explicit `codex` run to the configured secondary profile restarts only the
  matching service and leaves saved work paused for an explicit resume.
- DONE: `rtk pytest -q -o pythonpath=src tests/test_live_config.py tests/test_live_dashboard.py tests/test_live_shift.py tests/test_live_start.py tests/test_live_events.py tests/test_live_parallel.py tests/test_live_core.py tests/test_live.py`
  exited 0: **81 passed, 17 skipped**. The 17 shift integration fixtures skip
  because `codex_shift.launcher` and a `codex-shift` executable are absent in
  the current environment; no test failed. `rtk test node --test
  tests/test_dashboard_model.mjs`, `rtk proxy node --check
  src/ctfh_core/dashboard/app.js`, dashboard/start help, and
  `rtk git diff --check` exited 0. Selector, service forwarding, default
  restoration and displayed command were exercised by local fixtures.
- NOT_RUN: actual shift connection, Codex model turn, dashboard service restart
  and CTF browser flow in this environment. `shutil.which("codex-shift")`
  currently returns None, and no `codex_shift` package was found. The saved
  contest still names `codex-shift` as its engine, so it cannot resume here
  until that dependency is available. No saved contest state was rewritten.
- DONE: reconciled `rtk git status --short`; unrelated worktree edits remain
  untouched, and no commit was made.

## Direct Codex execution after shift removal — 2026-10-08

User correction: `모두 저장된 대회의 codex-shift 경로 지웠어 삭제해주고 codex나 codex-secondary로 갈거야`.
The intended live engine is now direct Codex for either selected account.

- DONE: removed the shift router/relay implementation, shift-only tests and
  optional package extra. New native contests select `codex`; saved shift
  contests migrate to `codex` when opened for execution. Advanced CLI no longer
  accepts `--engine codex-shift` or `--codex codex-shift`.
- DONE: retained direct `CodexExecRunner` for site control and concurrent
  per-problem turns. `--codex codex` starts the main CLI; CTFH's
  `--codex codex --secondary` selects the installed `codex-secondary` wrapper.
  The project profile remains the default when no argument is supplied.
- DONE: acquired each contest's exclusive runner lock and migrated all three
  saved modern shift rows: `contest-8f3f3aec9bc3`, `current`, and
  `h4ckinggame`. Each now stores `engine='codex'`. The migration asserted the
  global native thread ID, challenge count, turn count and per-problem thread
  count were unchanged. The respective turn counts were 4, 1 and 1; all three
  currently have zero discovered challenges. Legacy fixture DBs had no engine
  column and were left untouched. No contest was run or reset by migration.
- DONE: `live check-engine` now checks the selected Codex CLI's local login
  status without a model turn. Both `rtk proxy ./ctfh2 live check-engine
  --codex codex` and `rtk proxy ./ctfh2 live check-engine --codex codex
  --secondary` exited 0 with `status=ready` and `model_turn_started=false`.
  Installed `codex exec resume --help` confirms the direct runner's resume,
  JSON, output-schema and permission arguments are supported.
- DONE: focused Python regression
  `rtk pytest -q -o pythonpath=src tests/test_live_config.py tests/test_live_dashboard.py tests/test_live_start.py tests/test_live_events.py tests/test_live_parallel.py tests/test_live_core.py tests/test_live.py`
  exited 0: **78 passed**. After the final native selector guard and dashboard
  restart fix, the affected config/dashboard/start/parallel subset exited 0:
  **50 passed**; dashboard-only suite exited 0: **29 passed**. The test of a
  saved old-engine state asserts direct exec receives the old native thread ID.
  `rtk test node --test tests/test_dashboard_model.mjs` and
  `rtk git diff --check` exited 0.
- DONE: restarted the existing user-systemd dashboard on port 8788. The first
  attempt stopped the old unit but immediately hit a transient port-busy check;
  `check_port` now sets `SO_REUSEADDR` for sockets left in TIME_WAIT. A new
  service started, and a subsequent explicit `--restart` exited 0. `/health`
  reports the selected direct `codex` binary; `/api/state` reports the saved
  contest engine as `codex` and zero running workers. The service is active.
- DONE: README, live guide and Decision 23 now describe direct execution and
  the retired shift dependency. Direct headless `codex exec` has no interactive
  approval channel into the dashboard; the secondary profile keeps the user's
  explicit full-access setting while the main profile retains its own defaults.
- NOT_RUN: a real model turn, browser login or CTF solve. The latest saved
  contest still shows its prior failed state until the user explicitly resumes;
  the previous failure history is retained. The native thread IDs have not
  been exercised through a real `codex exec resume` in this session.
- DONE: reconciled `rtk git status --short`. Existing unrelated edits were not
  reverted or committed, and no global Codex account settings were changed.

## Terminal dashboard and browser connection — 2026-10-08

User correction: after requesting web dashboard removal, the user said a
dashboard is needed in the terminal and raised concern about TUI quality.

- DONE: stopped `ctfh-dashboard-d30051b63813-8788`; `rtk proxy systemctl
  --user is-active ctfh-dashboard-d30051b63813-8788` returned `inactive`.
  Removed the web dashboard app, launcher, static assets, dashboard-only
  approval scope, tests, CLI command and optional dependency.
- DONE: added a Textual terminal dashboard to the interactive URL shortcut.
  It displays all problems, selected problem details, live progress, counts,
  and work state. `D` marks only the selected problem solved, `F`/`A` filters
  events, `R` resumes after login, and `Q` cancels the controller. `--plain`
  preserves the former line-oriented terminal view, and non-TTY callers use it.
  Headless tests cover layout data, manual solve, login handoff, and CLI route.
  A sample-data SVG preview was generated at
  `docs/images/ctfh-terminal-dashboard.svg` and visually inspected after
  rendering it with a finite local headless Chrome command.
- DONE: corrected the browser helper to use `chrome-use --session <saved>`.
  Interactive start now opens the site in the connected Chrome before starting
  model turns. Updated manual helpers. Upgraded chrome-use 1.5.149 to 1.5.178;
  extension 0.5.30 and relay are connected. An explicit saved-session `open`
  of the saved Dreamhack page returned exit 0 after the upgrade.
- DONE: `rtk proxy ./ctfh2 live check-engine --codex codex --secondary`
  returned `status=ready` and `model_turn_started=false`.
  `rtk pytest -q -o pythonpath=src tests/test_live_config.py
  tests/test_live_start.py tests/test_live_tui.py tests/test_live_events.py
  tests/test_live_parallel.py tests/test_live_core.py tests/test_live.py`
  exited 0: **54 passed**. `rtk proxy ./ctfh2 start --help` and
  `rtk git diff --check` exited 0. `rtk git status --short` was reconciled;
  existing unrelated changes remain, and no commit was made.
- NOT_RUN: a new native Codex model turn or external CTF solve. The host can
  reach Chrome, but the earlier ordinary `codex` child turn's browser failure
  has not been isolated to permissions versus other child-environment causes.
  The user-configured secondary profile retains its explicit full-access flag.

## Ordinary Codex default — 2026-10-08

The user clarified that an invocation without a launcher argument should run
ordinary `codex`, not `codex-secondary`.

- DONE: changed checkout-local `ctfh.toml` to `binary="codex"` with native
  permission defaults. Explicit `--secondary` still selects the secondary
  wrapper and preserves the previously requested full-access mode via
  `[native.secondary]`. The terminal preview was regenerated with `codex`.
- DONE: `rtk proxy ./ctfh2 live check-engine` returned `command="codex"`,
  `status="ready"`, `model_turn_started=false`. The explicit
  `--codex codex --secondary` check returned `command="codex --secondary"`,
  `status="ready"`, `model_turn_started=false`.
- DONE: `rtk pytest -q -o pythonpath=src tests/test_live_config.py
  tests/test_live_start.py tests/test_live_tui.py` exited 0: **21 passed**.
  `rtk git diff --check` exited 0. No native model turn was started.
- NOT_RUN: whether ordinary `codex` can open the browser from a headless model
  turn under its current permissions; the earlier failure remains relevant.

## Summary-first terminal dashboard — 2026-10-08

The user requested fewer global log lines and detailed progress on selecting
each problem.

- DONE: the bottom pane now streams only important contest results: solves,
  submission starts/verdicts, failures, interruptions, worker stops, and
  approval requests. The full journal remains in SQLite.
- DONE: selecting a problem by mouse or keyboard loads its recent detailed
  events in the right pane and continues updating that pane. The F/A filter
  keys were removed. The right pane now fits the work note and next action
  above the detailed events. The sample-data SVG preview was regenerated and
  visually inspected after a finite local render.
- DONE: at 80×24 the layout keeps five visible lines of per-problem detail;
  the global result pane becomes shorter. The refresh timer stops on unmount
  so it cannot query widgets during teardown.
- DONE: headless UI coverage asserts that routine agent/tool chatter does not
  enter the global result pane and that clicking a different problem replaces
  the detailed event view. The focused regression command
  `rtk pytest -q -o pythonpath=src tests/test_live_config.py
  tests/test_live_start.py tests/test_live_tui.py tests/test_live_events.py
  tests/test_live_parallel.py tests/test_live_core.py tests/test_live.py`
  exited 0: **56 passed**. `rtk git diff --check` exited 0.
- DONE: after rendering the preview, `chrome-use status --json` still reported
  zero active sessions. Three CTFH sessions appeared during the final check;
  no Codex contest process was running, and `chrome-use close --all --force`
  closed all three. No model turn or contest run was started for this UI change.

## URL-driven discovery and ordinary Codex browser repair — 2026-10-09

User correction: each CTF site has its own layout. Given a URL, the agent must
inspect the live site at that time; a fixed Dreamhack template is not wanted.

- DONE: diagnosed the saved Dreamhack contest's 0-problem state. The host Chrome
  could read the logged-in competition page, but an ordinary `codex exec`
  child failed to write `/run/user/1000/chrome-use/*.lifecycle.lock` in its
  sandbox. Adding that path alone then failed to connect the daemon. A scoped
  `--add-dir` plus `sandbox_workspace_write.network_access=true` let the child
  read the existing Chrome session. The path is derived from
  `XDG_RUNTIME_DIR`, and the flags apply only to normal live contest turns.
- DONE: moved these Codex options before `exec`, including for `exec resume`;
  the first attempted live verification exposed that resume rejects them after
  the subcommand. The existing native thread ID was retained.
- DONE: clarified runtime site discovery in the prompt. A discovered problem
  can use its title as a stable key and the list URL if the site exposes no
  direct challenge link. Discovery/monitor turns now leave problem-specific
  work to solver turns; unexpected work entries are excluded with an event.
- DONE: bounded live verification command
  `rtk proxy ./ctfh2 live run --state .ctfh-live/contest-8f3f3aec9bc3 --codex codex --max-turns 1 --turn-timeout 180`
  exited 0. The resumed agent read Dreamhack in Chrome and stored 5 observed
  problems: 4 site-verified solved and 1 open (`Ring of IO`). The site uses
  problem card popups; no site-specific connector was added. This was a
  discovery turn only; no new flag submission or solver turn was run.
- DONE: focused regression
  `rtk pytest -q -o pythonpath=src tests/test_live_config.py tests/test_live_start.py tests/test_live_tui.py tests/test_live_events.py tests/test_live_parallel.py tests/test_live_core.py tests/test_live.py`
  exited 0: **58 passed**. `rtk git diff --check` exited 0.
- DONE: four additional Chrome sessions that appeared during the diagnostic
  live turns were closed individually. The saved contest's own session remains
  available; no `codex exec` or `ctfh2 live run` process remains.
- IN_PROGRESS: `Ring of IO` is stored as open. Continuous solver execution was
  not started by this bounded discovery check.

## One-URL full-access solver contract — 2026-10-09

The user clarified that a URL should lead to automatic login through existing
Chrome/account access, discovery of all problems, a separate unrestricted
Codex session for each unsolved problem, automatic flag submission, and local
flag/write-up storage.

- DONE: every native solver turn now receives
  `--dangerously-bypass-approvals-and-sandbox`, including when the ordinary
  `codex` account is selected. Discovery/monitor turns keep their configured
  access. The URL shortcut exits when all discovered problems are solved.
- DONE: ordinary `codex` child turns and `live check-engine` now use
  `~/.codex` rather than inheriting a caller's `.codex-secondary` home.
  The installed secondary wrapper continues to set its own account home.
- DONE: solver prompt requires the exact accepted flag bytes in private
  `flag.txt` and a private `writeup.md` in the assigned workspace. The
  controller checks both files and the flag hash before committing an agent's
  accepted submission. Previously solved external problems are not assigned
  invented flags.
- DONE: focused regression
  `rtk pytest -q -o pythonpath=src tests/test_live_artifacts.py tests/test_live_parallel.py tests/test_live_config.py tests/test_live_start.py tests/test_live_tui.py tests/test_live_events.py tests/test_live_core.py tests/test_live.py`
  exited 0: **63 passed**. The end-to-end fake-runner case covers discovery,
  accepted browser receipt, submission journal, private artifacts, and solved
  state. The full `rtk pytest -q -o pythonpath=src` suite exited 0:
  **176 passed**. Both `rtk proxy ./ctfh2 live check-engine
  --codex codex` and its `--secondary` variant exited 0 without a model turn.
  `rtk git diff --check` exited 0.
- NOT_RUN: real flag submission and write-up generation on Dreamhack.
  The saved Dreamhack state still has one open challenge and no solver turn.

## Token dashboard and per-contest thinking — 2026-10-09

The user asked for token counts beside problems, an `xhigh` thinking option,
and clarification of the native runner after a secondary-account solver error.

- DONE: Codex `turn.completed` input/cached-input/output counts are persisted
  on their own turns. The TUI shows per-problem input+output totals, contest
  total, and exact selected-problem components. An unreported usage stays `—`.
  Existing contest databases migrate by adding nullable turn columns.
- DONE: the URL shortcut and advanced live commands accept `--thinking xhigh`
  (alias `--reasoning-effort`) and optional `--model`. Selections persist with
  the contest and apply to new and resumed native `codex exec` turns. The
  saved contest's model and reasoning override are visible in `live status`;
  null means the account configuration supplies the default.
- DONE: `rtk pytest -q -o pythonpath=src` exited 0: **177 passed**.
  The 80-column Textual render includes the token column. `rtk git diff
  --check` exited 0. `rtk proxy ./ctfh2 start --help` shows the options.
- OBSERVED: the user's latest Dreamhack screenshot and saved status show a
  secondary solver turn failed with `cyber_safety`. The later saved status
  shows two failed attempts. The
  account configs currently default to `gpt-6-astra` and `xhigh`; the contest
  stores no model or reasoning override. `Ring of IO` remains open (4/5
  solved). This is a provider-side rejection, not a TUI or `exec resume`
  failure. No real accepted flag submission has been verified.
- DONE: the TUI `S` dialog now edits or clears this contest's model and
  reasoning overrides. Its saved values apply when the next discovery,
  monitor, or solver turn starts; running turns retain their launch settings.
  A Textual test verifies the dialog and SQLite persistence. A controller
  test changes the saved settings between solver turns and verifies that the
  next `CodexExecRunner` receives the new model and effort.
- DONE: final `rtk pytest -q -o pythonpath=src` exited 0: **179 passed**.

## Bilingual usage guide and GitHub publication — 2026-10-11

User direction: publish this project under `yc54616` on GitHub and revise both
Korean and English READMEs so a new user can understand how to operate it.
The user explicitly selected a public repository.

- DONE: rewrote `README.md` in English and added matching `README.ko.md` with
  language links, prerequisites, editable installation, a first contest run,
  dashboard/tmux controls, pause/restart behavior, configuration, saved files,
  submission semantics, and troubleshooting. Distinguished the default native
  runner from the older live controller and isolated benchmark runtime.
- DONE: checked commands/defaults against the current CLI and native runner.
  Documented full host access, the external `codex-secondary` prerequisite,
  required Codex flags, plaintext optional credential storage, checker option
  persistence, `approve` queuing, and `--no-submit` completion semantics.
- DONE: moved commented default options in `ctfh.toml` before
  `[native.secondary]`, so uncommenting them configures `[native]`. Parsed
  before/after TOML values are identical; no runtime behavior was changed.
- DONE: `rtk test timeout 240s pytest -q -o pythonpath=src` exited 0:
  **241 passed in 30.30s**.
- DONE: `rtk proxy python3 -m venv .venv` and
  `rtk proxy .venv/bin/python -m pip install -e .` exited 0 in a fresh virtual
  environment. The installed `ctfh2` and `ctfh-learn` help commands work.
- DONE: a finite Python documentation check validated 42 local links/anchors,
  syntax-checked 20 Bash blocks with `bash -n`, parsed 20 CLI examples, and
  parsed/matched both TOML examples. `rtk git diff --check` exited 0.
- DONE: checked 245 existing Git-history blobs for common token/private-key
  signatures and tracked credential/flag filenames; no matches were found.
  Local contest runs and `.venv` remain ignored. This is a bounded pattern
  check, not a guarantee that every possible secret format was detected.
- DONE: `rtk gh repo create yc54616/ctfh-core --public --source . --remote
  origin --push` (with the project description) exited 0. Published
  <https://github.com/yc54616/ctfh-core>, retaining the existing `master`
  branch and history. `origin/master` is now the upstream.
- DONE: `rtk gh repo view yc54616/ctfh-core --json
  nameWithOwner,url,visibility,defaultBranchRef,description` confirmed PUBLIC.
  GitHub API verification matched commit
  `73bb160a951afd5a1742632717e458a2fef4c096` to local HEAD and matched the
  remote blobs for both READMEs and `ctfh.toml` to local `git hash-object`
  results. This publication record is committed as a follow-up.
- NOT_RUN: real model turns, contest execution, or flag submissions for this
  documentation/publication task. Local tests are not live solve evidence.
- No `.planning/STATE.md` or `.intent/INTENT.toml` exists in this checkout;
  this existing status document remains the session record.
