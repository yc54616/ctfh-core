# Architecture Decisions

Date: 2026-10-04

## Current product direction — user clarification, 2026-10-08

Latest implementation scope: the user said, "codex는 내가 연결할게 -> 일단 ctf
agnet 완성해줘". Finish the contest agent, its connection contract, browser
evidence capture, durable challenge work, submission/recovery loop, and local
integration verification. The user owns the later Codex connection. Do not
make account approval or an actual model connection a prerequisite for this
delivery, and do not equate deterministic fixture success with model solve
capability. No live CTF submissions are needed for this implementation scope.

Later correction: "푼 문제는 내가 입력도 해줄거야 ... 모든 문제를 한번에 돌리는거야".
The user confirmed both input paths: a flag submitted directly on the CTF
site, and an explicit solved-problem command to the agent. The live controller
now launches every discovered ready problem concurrently by default. Each
problem has its own agent thread, browser session, and work state; a separate
browser monitor detects site solves. Either input marks that problem solved and
stops only its worker. A user-marked solve does not fabricate an accepted
candidate submission. The optional `--max-workers` cap is user controlled.

The product goal is a continuous browser-driven CTF solver. The user supplies a
CTF site URL and account access at run time. The existing Codex CLI opens the
browser, logs in, explores the site, reads challenges and downloads attachments,
solves challenges, submits candidate flags, checks the site's response, and
continues with the next challenge.

The user explicitly corrected the proposed site-integration approach:

> 연결하는게 아니라 주면 바로 브라우저 켜서 로그인하고 진행하는거야

Do not require a site-specific connector, API integration, or a preselected
platform before this workflow can run. Native Codex should operate the site's
browser interface using the tools already configured by the user.

The intended native environment includes:

- intent-gate for preserving the user's objective and project context;
- the existing MCP and token/output optimization setup, including RTK and
  context-mode where applicable;
- chrome-use first, reusing the connected Chrome profile where possible; the
  built-in Codex chrome plugin is only a final compatibility fallback;
- persistent Codex sessions and durable challenge/run progress so a restart or
  context rollover can continue the same work.

Intent records, native conversation history, and run/challenge/submission state
have different roles; preserving one does not automatically preserve the others.

The V3 runtime and benchmark machinery are existing foundations and validation
tools. Their completion does not establish completion of the automatic solver.
The current smoke path creates an isolated configuration that disables native
memory/hooks/plugins and launches Codex with `--ephemeral`; it does not yet
provide the requested native-tooling continuity or browser-driven contest loop.

The next implementation scope is to preserve the configured native tool/session
environment and give Codex a durable browser-driven solve/submit/continue
workflow. The site response must determine whether a submission was accepted.
There must be a resumable handoff when a login challenge requires the user.
These are product requirements from this clarification, not claims that the
current runtime implements them. Earlier benchmark isolation decisions describe
the existing evaluation profile and must not be mistaken for the live solver's
product objective.

## Decision 1 — Thin supervisor, not a second agent framework

Accepted.

Native agent harnesses are first-class execution units. Codex CLI, Claude Code
and future native agents keep their own reasoning/tool/context semantics.

CTFH supervises lifecycle, isolation, verification and timing.

Reason:
- CAGE/AgentCyberRange evaluates integrated native harnesses.
- recent studies show strong general-purpose native agents are already difficult
  baselines to beat.
- duplicating native loops creates latency and hides provider-specific strengths.

## Decision 2 — Environment and agent are separate benchmark dimensions

Accepted.

A model name is insufficient. A deployed profile includes native agent version,
effective model, environment image/tooling, prompts/context/skills, strategy,
browser configuration and deadline/concurrency parameters.

The experimental unit is one task instance × frozen RunLock × evaluation
protocol × repetition.

## Decision 3 — Split TargetProvider from WorkerEnvironment

Accepted after Astra review.

Target lifecycle and attacker/worker lifecycle are different:
- restarting a worker must not reset a remote challenge target
- parallel attempts may use independent workers while sharing or cloning targets
- target epoch controls effect freshness

## Decision 4 — One kernel loop, one authoritative writer

Accepted after Opus 5.5 review.

Do not run per-task state coordinators with competing SQLite writers.

Use:
- one host lock
- one kernel epoch
- one bounded inbox
- one SQLite writer
- short batched commits
- dispatch only after causal state commit

## Decision 5 — Environment is the local transaction boundary

Accepted.

Do not journal every shell/tool call.

Ordinary local effects happen inside a disposable, attempt-owned environment.

Journal only consequential external effects that escape that boundary.

## Decision 6 — No universal lease framework in v0

Accepted.

A lease row cannot revoke an unmediated browser socket/process.

v0 isolation:
- private environment per attempt
- OwnerTag
- host epoch
- hard process-tree kill
- orphan sweep
- exclusive resource transfer only after old owner is confirmed dead

Specific brokers/leases are introduced only for actual shared resources.

## Decision 7 — UNKNOWN belongs to external operations, not attempts

Accepted.

Attempt lifecycle and external-delivery uncertainty are different facts.

A worker may terminate cleanly while a remote submission remains UNKNOWN.

## Decision 8 — No global base-revision stale rejection

Accepted after Sol/Astra review.

Concurrent evidence should not be invalidated because unrelated facts changed.

Use:
- target instance + target epoch for action freshness
- attempt identity/owner tag
- final outcome compare-and-swap
- evidence dependency provenance

## Decision 9 — Direct strong native agent is the baseline

Accepted.

Planner/executor, heterogeneous mixing and parallel strategies must beat direct
baseline on verified points by deadline.

Evidence:
- 200-task study: Pro/Pro greatly outperformed Pro/Flash and Flash/Pro in its
  tested D-CIPHER configuration.
- 30-task second-look study: direct GPT-5, evaluator, and planner pipelines had
  the same peak 19/30 coverage while orchestration increased elapsed time.

## Decision 10 — pass@k before complex multi-agent orchestration

Planned, not v0.

After baseline:
- independent pass@k
- then hedged pass@k (launch extra attempts after historical p50 if unsolved)
- cancel siblings only after authoritative success

This is simpler to measure and isolate than shared-context multi-agent teams.

## Decision 11 — Browser is a capability, not a global control plane

The original isolated-worker capability decision was accepted after Sol/Astra
review. The user's 2026-10-08 live-product instruction sets a different browser
priority for the continuous contest workflow.

Live contest workflow:
- open the browser for site login and challenge discovery at startup;
- use chrome-use first, preferably its connected authenticated Chrome profile;
- use the built-in Codex chrome plugin only as a final compatibility fallback;
- keep one explicit browser owner and never silently migrate auth state.

Existing isolated-worker benchmark profile:
- open a browser only when HTTP/shell/source tools are insufficient;
- GhostChrome is the managed browser capability for those isolated attempts;
- Jev may later overlay action decisions without becoming a second browser owner.

## Decision 12 — Context systems are experimental views

Accepted.

Do not blindly stack native compaction + RTK + context-mode + codebase-memory.

- RTK: output-compaction view only
- context-mode: large-output sandbox/index only when benchmarked useful
- codebase-memory: large source-tree structural retrieval only when useful

Raw/provenance rules apply before transformations where the underlying agent
exposes raw output.

RTK rewrite/auto-allow is not an authorization boundary.

## Decision 13 — Skills later, using Agent Skills format

Accepted.

Technique/workflow knowledge is packaged with progressive disclosure and hashed
into RunLock.

Do not build skill loading into the kernel.

## Decision 14 — Static benchmark scores are not promotion evidence alone

Accepted.

CTFusion shows contamination/search-time solution retrieval can inflate static
CTF benchmark results.

Promotion requires fresh/live/private holdouts and leakage controls.

## Decision 15 — No generic plugin ecosystem in v0

Accepted by all three reviews.

Explicit factories/configuration first.

Only after multiple real implementations reveal stable replacement pressure do
we freeze a Protocol and add Python entry-point discovery.

## Decision 16 — Reuse codex-shift through native app-server

The user proposed `codex-shift`, clarified that it is the same Codex, and then
authorized continuation with `계속해줘` on 2026-10-08.

- Add `live run --engine codex-shift` and persist the engine selection.
- Reuse the installed `codex_shift` router/relay; do not alter that checkout.
  The TUI launcher cannot serve as an exec binary: native `--remote` rejects
  `exec`. Use Codex app-server JSON-RPC over local Unix WebSockets instead.
- Share one owned server/router while keeping control and problem threads
  separate. Preserve the native thread IDs when switching native engines.
- Disable relay retries; CTFH owns turn accounting and submission recovery.
  Keep native permissions and approvals. Authentication, quota, rate limits,
  and policy denials stop the controller.
- Target cancellation at a single native turn and confirm its terminal event.
  Reap the owned server when the controller exits.
- `live check-engine` performs a handshake without a model turn. Protocol
  fixtures prove connection behavior; they do not prove account access or
  autonomous solving on the external CTF site.

Protocol references: https://developers.openai.com/codex/app-server and
https://developers.openai.com/codex/cli/reference.

## Decision 17 — Show public activity while problems are running

On 2026-10-08 the user requested visibility into how the AI is solving problems
through events. Turn-end summaries alone do not satisfy that request.

- Record public progress, plan steps, tool lifecycle, checkpoints, submissions,
  and solve/stop events with their assigned problem and turn.
- Use task-local sinks so concurrent native and custom connections cannot
  overwrite one another's event destinations. Retain the latest 5,000 events
  in the private contest DB and provide `live events --follow` with filters.
- The observer does not own or cancel workers. Event output is not verdict
  evidence and does not grant permissions or change challenge state.
- Exclude raw tool arguments/output, private reasoning, and complete final
  reports. Bound and redact public prose, without claiming arbitrary secret
  detection. This history begins with controller runs using the new code.

## Decision 18 — Site-only startup and a single operating screen

On 2026-10-08 the user rejected the multi-command usage flow as too complex
and requested automation with minimal input.

- `ctfh2 URL` starts the contest; bare `ctfh2` resumes the last contest or asks
  for the URL on first use. Choose a private state path automatically, reuse
  matching existing states, and persist the last selection within this repo.
- Preserve stored connection choices. A new contest prefers installed shift,
  otherwise native Codex. The shift runtime checks its own connection before
  starting a model turn. Failure never triggers silent engine fallback.
- Show problem numbers and live events inline. Accept `목록`, `완료 번호`, and
  `종료`; keep all-ready-problem parallelism and targeted cancellation.
  Follow-up clarification: collect the challenge list first, then start the
  unsolved problem workers concurrently. Each worker reads its own statement
  and attachments; later monitor discoveries are added to the running contest.
- Login handoff waits for an actual fresh Enter after the user completes the
  browser step. No input means no resume. Noninteractive runs retain handoff
  state. Cancelable terminal input must not leave a blocking executor thread.
- During verification the installed shift implementation had changed: its
  default became command-count switching, and thread/resume reset router state.
  Preserve CTFH's existing three-successful-turn cadence through supported
  `--base-turns 3` configuration and a local router adapter that retains state
  on routine reattachment. Keep denial holds; explicit new native threads and
  runtime restarts start fresh. No edits are made to the shift checkout.

## Decision 19 — Local dashboard over the existing live controller

On 2026-10-08 the user proposed a dashboard and authorized implementation with
`가보자`. Keep the site-only starting flow and expose operating controls in one
local screen.

- `ctfh2 dashboard` starts or reuses a detached systemd user service and opens
  connected Chrome through a named chrome-use session. Bind to loopback only;
  never bypass denied local socket access through another execution context.
- A small ASGI app and packaged static HTML/CSS/JavaScript use the existing
  live controller, SQLite store, engine selection and public event journal.
  Avoid a separate solver framework or per-site integration layer.
- URL and start are the primary inputs. Account/vault/concurrency options are
  collapsed. Show all problems, status counts, problem-scoped activity and
  checkpoints; poll incrementally and retain bounded public events.
- Manual solves cancel only the matching worker. Pause awaits controller
  cleanup. Login handoff requires the dedicated explicit resume action.
  Refresh does not stop work; server restart restores saved progress without
  automatically spending model turns. Other CLI owners cannot be paused here.
- Protect control requests with loopback/Host/Origin checks, a per-process
  token and a current-contest identifier. Accept neither arbitrary paths nor
  shell commands. Render untrusted text through textContent and restrict links.
- Distinguish socket-free HTTP/controller tests and static rendering from a
  real browser/server/account acceptance run. The latter is not yet verified
  because this execution environment denies local sockets.
- Follow-up fix after the user's `Not Found` startup report: classify HTTP
  errors and mismatched health responses as occupied ports. With no `--port`,
  first reuse a healthy matching dashboard in 8787–8796, otherwise select a
  free port there. An explicit port remains mandatory. Permission failures
  remain fatal and never trigger alternative dispatch; health probes do not
  follow redirects to other services.

## Decision 20 — Use the user's secondary-account launch mode

The user reported that discovery failed at a native command approval with zero
problems collected, then clarified: `아 codex-secondary --dangerously-bypass-approvals-and-sandbox 이걸로 들어가는거야`.
This supersedes the prior assumption that an unconfigured `codex app-server`
launch matched the user's intended native session.

- Select the installed `codex-secondary` wrapper through checkout-local
  `ctfh.toml`; it owns secondary-account and credential-store selection.
  Keep shift routing and existing native thread IDs. Native exec receives the
  requested CLI flag. App-server uses process-scoped config overrides plus
  explicit equivalent thread/start, thread/resume and turn/start policies.
  No global Codex or installed wrapper/shift files are modified.
- The implementation defaults to normal native permissions without the explicit
  project setting. A different explicit CLI binary does not inherit this
  wrapper's full-access opt-in. Authentication, managed restrictions, quotas,
  and model policy denials retain their existing stop behavior.
- Command/file approval requests under other configurations can wait for an
  explicit dashboard decision. Native request IDs are scoped to the active
  thread/turn, and UI IDs are unique across parallel workers. Only individual
  accept/decline/cancel responses are supported; no automatic response grants
  approval or amends a persistent policy. Preview data is ephemeral and is
  excluded from the public event journal. Missing/truncated previews cannot
  be accepted. Cancellation and serverRequest/resolved clear pending requests.
- Provide `dashboard --restart` to replace the already-running local service
  with the updated code. A healthy matching checkout/port must be identified
  before stopping its unit. Actual model execution is not part of the coding
  sandbox verification; use socket-free protocol/controller fixtures here.

Official references read for the mapping and protocol:
https://developers.openai.com/codex/app-server and
https://developers.openai.com/codex/cli/reference.

## Decision 21 — Select the native launcher per invocation and validate wire values

The user requested `codex` versus `codex-secondary` as an argument and reported
`rpc_failed: app-server rejected thread/resume`.

- Support `--codex codex|codex-secondary` on `dashboard`, `start`, URL shortcuts
  and last-contest resume. Pass the explicit override through the detached
  service command and controller without rewriting `ctfh.toml`. Show the
  effective launcher in dashboard state, health and the UI. The existing
  project profile remains the default; a different explicit launcher retains
  its own permission defaults as recorded in Decision 20.
- An explicit selection differing from the running service restarts only the
  matching checkout/port service. A matching selection reuses it. Restored
  progress remains paused until the user resumes; no thread state is reset.
- Correct the thread/start and thread/resume sandbox value to
  `danger-full-access`. The turn/start `sandboxPolicy.type` remains
  `dangerFullAccess`. The prior fake server accepted arbitrary thread values
  and did not catch this mismatch. Make the fixture reject invalid values and
  compare emitted start/resume/turn parameters against JSON schemas generated
  by the installed Codex CLI. A permissive mock or documentation sample alone
  is insufficient evidence of compatibility with the installed version.
- RPC failures include the native error code and bounded, redacted message.
  Raw request arguments and error data are not copied to public progress.

## Decision 22 — Present the account choice as codex and codex --secondary

The user corrected the launcher interface to `codex` or `codex --secondary`.
The installed upstream `codex --secondary --help` exits 2 with an unexpected
argument error. Therefore `--secondary` is a CTFH option. The CLI maps it to
the installed `codex-secondary` account wrapper without passing that flag to
upstream Codex. This works for dashboard, URL start, last-contest resume and
advanced live run/check-engine; the detached dashboard forwards the same
selection. Keep the old `--codex codex-secondary` spelling accepted for saved
commands. Show `codex --secondary` in the CTFH UI/terminal and record the
actual binary separately in API metadata. Preserve the current checkout
default and shift routing.

## Decision 23 — Remove shift routing and run the selected Codex directly

The user explicitly removed `codex-shift` from the desired architecture:
`모두 저장된 대회의 codex-shift 경로 지웠어 삭제해주고 codex나 codex-secondary로 갈거야`.
This supersedes the shift-dependent parts of Decisions 16 and 20–22.

- New and saved native contests use `CodexExecRunner` with the selected
  `codex` or `codex-secondary` executable. Keep one native thread per site
  control/problem and the existing concurrent worker and submission journal.
  `--secondary` stays a CTFH argument mapped to the installed account wrapper.
- Retire `ShiftRuntime`, the router/relay and their optional dependency/tests.
  The `check-engine` command checks local login status for the selected native
  account without starting a model turn.
- Migrate saved `engine='codex-shift'` rows to `engine='codex'` under the
  contest lock. Preserve native thread IDs, challenge/submission rows and
  turn history. Future old states migrate when opened for execution.
- Direct headless Codex exec has no interactive approval channel into the
  dashboard. The user's configured secondary account retains its explicit
  full-access setting; the normal account keeps its configured permissions.
  Do not silently reset native threads or change global account settings.

## Decision 24 — Use a terminal dashboard for the live contest

The user first asked to remove the web dashboard and return to the terminal,
then clarified that a dashboard is still needed in the terminal. This
supersedes the web dashboard parts of Decisions 20–23.

- The interactive `ctfh2 URL` and `ctfh2` commands use a Textual terminal
  dashboard. It shows every discovered problem, the selected problem's work,
  and live events. The user can mark a selected problem done, filter its events,
  resume after browser login, and stop the controller. Noninteractive runs and
  `--plain` retain the line-oriented terminal interface.
- Remove the local HTTP dashboard/server/service and its package dependency.
  Keep the SQLite journal, concurrent workers, direct Codex execution, and
  manual solve semantics. The retired web dashboard has no active service.
- Before starting a native model from the terminal, open the contest in the
  user's connected Chrome. Pass chrome-use's `--session` option for the saved
  per-turn browser session; `--session-name` was a different setting and did
  not reliably pin the daemon session. A successful host browser connection
  does not establish permission inside a child Codex model turn.
- Preserve the user-selected secondary full-access default and the ordinary
  `codex` account's own permission defaults. Do not silently escalate either.

## Decision 25 — Make ordinary Codex the default launcher

The user clarified that launching the terminal dashboard should run `codex`,
not `codex-secondary`. The checkout default is therefore `codex` with its
native permission settings. Explicit `--secondary` maps to the secondary
wrapper and retains the previously requested full-access mode in
`[native.secondary]`. The terminal dashboard and plain path share this
selection. No global Codex account settings are changed.

## Decision 26 — Show important results first and per-problem detail on selection

The user found the global live event stream too noisy. The terminal dashboard
now shows only solve, submission, failure, interruption, and approval events in
its small contest-wide result panel. Clicking a problem or moving the table
selection loads that problem's detailed event history in the right pane and
continues streaming its events there. The prior manual F/A event filter is
removed. The full SQLite event journal and plain CLI event command remain
available for detailed inspection.

## Decision 27 — Analyze each URL at run time and allow scoped Chrome access

The user clarified that CTF sites differ: after receiving a URL, the agent
must inspect that site's live login, rules, challenge navigation, solve markers,
and submission controls. Do not predefine a Dreamhack form, fixed selectors,
or a site-specific connector. The report schema is only the internal progress
journal, not an assumption about a site's UI.

The ordinary `codex` child previously could not open the connected Chrome
session because its sandbox could not write the chrome-use lifecycle lock under
`$XDG_RUNTIME_DIR/chrome-use`; after adding that writable path, it also needed
network access to reach the browser relay. Grant both for these contest turns
with the workspace sandbox, without changing global Codex settings or the
explicit secondary account selection. Put the global CLI options before
`exec` so saved native threads can resume.

Discovery and monitor turns report observed challenges and site state. Their
per-challenge `work` entries are ignored; only assigned solver turns own those
checkpoints. Unknown solve markers stay unknown until verified from the site.

## Decision 28 — Give each solver full access and preserve accepted artifacts

The user clarified the desired one-URL workflow: reuse authenticated Chrome or
its saved login vault, discover all problems, start one independent Codex
session per unsolved problem with
`--dangerously-bypass-approvals-and-sandbox`, solve concurrently, submit flags,
and save flags and write-ups locally. The ordinary versus secondary option
chooses the account; it does not choose whether a solver gets full access.

- Discovery/monitor turns retain their configured access. Every native solver
  turn receives the full-access flag, regardless of the selected account.
- Ordinary `codex` uses `~/.codex` even when CTFH is invoked from a shell with
  `CODEX_HOME=.codex-secondary`; the secondary wrapper sets its own home.
- Accepted agent submissions require private `flag.txt` and `writeup.md` in
  that challenge's durable workspace. The exact flag bytes must hash to the
  journaled candidate. Raw flags stay out of CTFH's SQLite journal and public
  event stream. A previously solved external problem may have no available
  flag and must not be fabricated.
- The URL shortcut stops when every discovered challenge is solved. A later
  rerun can discover newly published problems. The advanced live command keeps
  its explicit `--stop-when-solved` option.

## Decision 29 — Show measured tokens and allow per-contest thinking settings

The user asked to see token use beside each problem and to select an `xhigh`
thinking level. Native `codex exec` remains the unattended runner: each
problem has a saved thread and subsequent turns use `codex exec resume`.

- Record the numeric usage from Codex `turn.completed` events per turn. The
  table shows each problem's input plus output token total; the selected
  problem shows input, cached input, and output separately. Cached input is
  already included in input. A rejected turn without reported usage remains
  unmeasured instead of showing a made-up zero.
- `--thinking xhigh` (also `--reasoning-effort xhigh`) and optional `--model`
  persist with the contest and override only its Codex turns, including
  resumed threads. Omission keeps the selected account's configuration.
- The TUI `S` dialog can set or clear those contest-level overrides. Its
  changes apply to newly launched turns while running turns continue.
- Model, reasoning effort, and host sandbox access are separate from the
  provider's cyber safety decision. The saved Dreamhack contest's secondary
  solver attempt failed with `cyber_safety`; changing the model or opening an
  interactive terminal is not assumed to remove that gate.
