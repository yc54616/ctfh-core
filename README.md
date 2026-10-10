# ctfh-core

Performance-first thin supervisor for authorized CTF competition agents.

## Design rule

CTFH does **not** replace Codex, Claude Code, or another native agent with a
second general-purpose agent framework.

It supervises native agents through a small set of enforceable boundaries:

```text
TaskSource
   |
TargetProvider
   |
KernelLoop ---- OutcomeAuthority
   |
WorkerEnvironment
   |-- native shell/files
   |-- optional interactive PTY/session service
   |-- optional managed GhostChrome browser service
   |
AgentAdapter
   |-- Codex CLI
   `-- Claude Code CLI
```

The kernel owns durable state, deadlines, recovery, target epochs,
cancellation, RunLock configuration, artifact publication, and authoritative
outcome commits. Native agents keep their own planning, conversation loop,
ordinary tools, context compaction, and native skill activation.

## V3 implemented capabilities

### Agent adapters

- Codex CLI
- Claude Code CLI
- common `AgentAdapter` contract extracted only after both real adapters were
  exercised

### WorkerEnvironment capabilities

- Bubblewrap attempt isolation and kill-domain ownership
- interactive PTY sessions through `ctfh-session`
  - open / send / read / signal / close / list
  - bounded in-memory and on-disk transcripts
  - cancellation-safe connected RPC shutdown
- lazy managed GhostChrome browser through `ctfh-browser`
  - attempt-local disposable browser profile
  - explicit WebSocket attachment to the attempt-owned Chrome
  - navigation domain allowlist support
  - browser lifecycle and controller processes tracked under WorkerOwnerTag

Neither session nor browser calls go through KernelLoop or SQLite.

### Skills and native configuration

Codex skills are materialized into an attempt-local snapshot before launch.

- content-addressed digest in RunLock
- symlinks dereferenced into the snapshot
- materialized tree digest rechecked
- read-only inside the sandbox

The native Codex configuration is also materialized and pinned so host
MCP/plugin/memory settings do not silently alter a benchmark run.

### Frozen knowledge

`memory=off` is the default.

A run may optionally use a frozen knowledge snapshot:

- source tree is content-addressed
- copied into attempt-local storage
- digest rechecked
- mounted read-only at `/ctfh-knowledge`
- exposed as `$CTFH_KNOWLEDGE_DIR`
- pinned in RunLock

There are no live shared-memory writes on the solve path.

### Trace and artifacts

Bulk agent/session/browser evidence stays out of KernelLoop/SQLite.

At attempt completion:

1. environment services are quiesced
2. service evidence is copied to attempt output
3. `trace-manifest.json` records stream hashes, sizes, coverage boundaries,
   and truncation accounting
4. evidence is made read-only
5. supervised attempts are streamed into content-addressed storage without
   buffering the whole attempt in RAM

### Offline WikiSkill-style learning

`ctfh_learning` is a separate package; `ctfh_core` does not import it.

Pipeline:

```text
sealed trace
  -> verify evidence
  -> append-only learning wiki
  -> candidate knowledge / one atomic skill
  -> held-out baseline-vs-candidate evaluation
  -> promote or reject
  -> versioned read-only snapshot
```

Promotion safeguards include:

- source-trace tasks cannot overlap promotion tasks
- promotion tasks cannot overlap the final untouched set
- promotion/final splits are immutable once committed
- invalid first evaluations cannot poison the split registry
- evaluation records are bound to RunLock hashes
- candidate evaluations are bound to the exact candidate content digest
- candidate provenance fields are rehashed on load
- each candidate has one terminal decision; reject cannot later flip to promote
- rejected hypotheses remain in the learning-side wiki but are not
  automatically agent-visible

## Quick smoke tests

From the repository root:

```bash
PYTHONPATH=src python3 -m ctfh_core.cli smoke \
  --agent codex \
  --mode pair \
  --model gpt-6-sol \
  --effort high
```

Enable the interactive session capability:

```bash
PYTHONPATH=src python3 -m ctfh_core.cli smoke \
  --agent codex \
  --mode pair \
  --interactive-session
```

Enable the managed browser capability:

```bash
PYTHONPATH=src python3 -m ctfh_core.cli smoke \
  --agent codex \
  --mode pair \
  --browser \
  --browser-domain example.ctf
```

Use a frozen knowledge snapshot:

```bash
PYTHONPATH=src python3 -m ctfh_core.cli smoke \
  --agent codex \
  --mode pair \
  --knowledge-snapshot ./knowledge-snapshot
```

Claude can be selected with `--agent claude`; its default model is
`claude-opus-5-5`. A valid local Claude login is required.

## Native contest runner (default)

`ctfh2 <URL>` now runs plain interactive Codex sessions instead of the live
wrapper. One extra checker verifies flags and writeups. Design and sources:
[docs/research/NATIVE_CODEX_SOLVER_CHECKER_2026-10-09.md](docs/research/NATIVE_CODEX_SOLVER_CHECKER_2026-10-09.md).

```bash
rtk proxy ./ctfh2 https://ctf.example.com/challenges            # collect, open tmux, attach
rtk proxy ./ctfh2 https://ctf.example.com/challenges --secondary --model gpt-6-sol --thinking xhigh
rtk proxy ./ctfh2                     # reattach to the current or last contest
rtk proxy ./ctfh2 status              # board: state, tokens, reason per challenge
rtk proxy ./ctfh2 done web-1 pwn-2    # you solved these yourself: their windows close
rtk proxy ./ctfh2 approve web-1       # trust the current flag.txt and submit it once
```

1. A `codex exec` collector logs in through connected Chrome (`chrome-use`,
   session `ctfh-collect`) and writes one folder per challenge.
2. A tmux session `ctfh-<contest>` opens a `checker` window plus one
   `codex --dangerously-bypass-approvals-and-sandbox` window per challenge
   (`--max-parallel`, default 0 = every challenge at once). Attach with `tmux attach -t ctfh-<contest>` and
   type into any window as usual.
3. The checker re-runs `solve.py` and requires the same flag. It submits each
   flag hash at most once through Chrome (`ctfh-submit`), reviews `writeup.md`
   blind-first, sends `[검사기] …` feedback into the solver window, and closes it
   when done.
4. The checker window is a keyboard dashboard (`--plain` or `plain = true` in
   `ctfh.toml` keeps the old text board):

   | Key | Action |
   |---|---|
   | Enter | jump to the selected challenge's window (back: tmux window list, default `Ctrl-b w`) |
   | `n` | nudge now; does not count against `--nudge-limit` |
   | `r` | reopen a closed window; also resumes `manual` and `hold` challenges |
   | `o` | read `work/NOTES.md` |
   | `p` | hold: close the window, no automatic restart until `r` (a teammate is on it) |
   | `d` | mark solved (a teammate solved it) and close the window |
   | `s` | rescan the site now (default every 60 minutes on `gpt-6-luna`, low effort) |
   | `+` / `-` | change the window limit live (kept across checker restarts until `--max-parallel` changes); limited slots open the most-solved challenges first |
   | `q` | quit the checker; solver windows keep running |

Checker safeguards:

- **manual**: after `--nudge-limit` (default 3) nudges or restarts, or 2 nudges
  with no change to `work/NOTES.md`, the checker stops nudging and leaves the
  challenge to you.
- **Pause**: when Codex is unreachable, out of usage (limit reached with no
  credits), or most windows show a Codex error, nudges, restarts, and rescans
  stop. One window retries a turn every 10 minutes. Once Codex works again,
  every stalled window gets one nudge.
- **Contest end**: the collector stores the end time in `.ctfh/site.json`
  (`ends_at` in `contest.toml` overrides it). The board counts down the last 24
  hours. At the end, unsolved windows go to `hold`. Accepted ones finish their
  writeups.
- **Fake flags**: for a remote challenge (`remote.txt`), a flag with
  fake/dummy/placeholder wording, or one found in the handed-out files
  (including inside zip/tar), goes back to the solver instead of being
  submitted.
- **Changing remote flags**: when a remote challenge's rerun prints a different
  flag in the contest format, the newer one replaces `flag.txt` and is submitted
  (instances that mint a flag per start). If the rerun fails while the remote is
  closed, the challenge waits for the server instead of counting as a wrong solve.
- **Attachments download in the background**: collecting only lists
  challenges and their attachments, so web/remote challenges start right away.
  A separate cheap Codex run (rescan model) fetches attachments smallest first,
  big forensics images last; each challenge opens as soon as its own files are
  complete (`files/.ctfh-done`). After 3 failed tries it starts with whatever
  arrived.
- **Checker restarts itself** after a crash (5 s later); `q`/Ctrl-C ends it, and
  5 quick crashes in a row stop the retries and leave the error on screen.
- **Cheaper side jobs**: submitting runs at low effort and writeup review at
  medium, on the solver model (`--submit-thinking`, `--review-thinking`;
  `same` follows `--thinking`).
- **Rules and usage**: the collector summarizes the contest rules onto the board
  and into every `AGENTS.md`. A ban on automation or AI, and weekly Codex usage
  of 90% or more, are flagged `!!`.

Folder contract (`~/Desktop/CTF/<site>/` by default, or `--dir`):

```text
contest.toml  AGENTS.md  .ctfh/{site.json,board.txt,collect.log}
challenges/<slug>/
  challenge.md challenge.json files/ work/NOTES.md   # collector / solver
  solve.py flag.txt writeup.md                        # solver output
  status.json .ctfh-check/                            # checker only
```

Login uses the Chrome profile you are already signed into. Optionally,
`~/.config/ctfh/credentials.toml` (mode 600, `["host"] username/password`) lets
the collector fill the login form through `ctfh2 secret-fill`. That command
passes the value via `chrome-use fill --from-env`, so the model never sees it.
2FA and captchas are handed back to you. Solver sessions run with full host
access, so they can read any file you can.

## Live contest agent (previous flow: `ctfh2 start`)

### Terminal shortcut

[Terminal dashboard preview (sample data)](docs/images/ctfh-terminal-dashboard.svg)

The executable `./ctfh2` runs directly from this checkout without installing the
package or setting `PYTHONPATH`:

```bash
rtk proxy ./ctfh2 start https://h4ckingga.me/challenges
```

The shortcut also accepts `--codex codex` or `--codex codex --secondary`, including
when resuming the last contest: `rtk proxy ./ctfh2 start --codex codex --secondary`.
`--secondary` is a CTFH option: the installed Codex CLI does not accept it
directly. CTFH uses the installed `codex-secondary` account wrapper for that option.

The shortcut opens the contest in connected Chrome, chooses the state directory,
and shows a terminal dashboard with the problem list and important contest
results. Clicking or selecting a problem shows its detailed events on the right.
The list shows each problem's accumulated input plus output tokens, and the
banner shows the contest total. Exact input, cached input, and output counts
appear in the selected problem's details after Codex reports a completed turn.
A turn rejected before usage is reported shows `—`.
It uses an existing matching contest
when available. New and saved native contests run directly through the selected
Codex CLI. This checkout defaults to ordinary `codex` from `~/.codex`.
Discovery and site-monitor turns use the workspace sandbox with Chrome session
and network access. Every problem-solving Codex turn uses
`--dangerously-bypass-approvals-and-sandbox`, including with the ordinary
account, so challenge tools such as QEMU can access the host when the user can.
Explicit `--codex codex --secondary` selects the previously requested secondary
account with full access.
To select the model and thinking level for this contest, use for example:

```bash
rtk proxy ./ctfh2 start https://h4ckingga.me/challenges --model gpt-6-sol --thinking xhigh
```

These choices persist when the contest resumes and apply to new and resumed
Codex turns. If omitted, the selected account's own settings apply. Model and
reasoning choices do not override the account's cyber safety gate.
In the terminal dashboard, press `S` to change them. Saved changes apply to
new turns; already running turns continue with their current settings.
If browser login needs your help, complete it in Chrome and press `R` in the dashboard.

Select a problem and press `D` to mark it solved, `R` to resume after login,
`S` to edit Codex settings, or `Q` to stop. All ready problems still run concurrently.
Use `--plain` for line-by-line output and the earlier `목록` / `완료 2` / `종료`
commands. To resume the last
contest, run just:

```bash
rtk proxy ./ctfh2 start
```

On the first invocation without a saved contest, it asks only for the site URL.
The shortcut and last-contest record are anchored to this checkout, so they
also work when the executable is invoked by absolute path from another directory.

### Advanced live commands

Use these when explicit state paths or a separate event viewer are useful:

```bash
rtk proxy ./ctfh2 live run https://h4ckingga.me/challenges --state .ctfh-live/h4ckinggame
rtk proxy ./ctfh2 live status --state .ctfh-live/h4ckinggame --details
rtk proxy ./ctfh2 live events --state .ctfh-live/h4ckinggame --follow
rtk proxy ./ctfh2 live mark-solved --state .ctfh-live/h4ckinggame --challenge stable-key
```

To check the selected account and run directly through its Codex CLI:

```bash
rtk proxy ./ctfh2 live check-engine --codex codex --secondary
rtk proxy ./ctfh2 live run https://h4ckingga.me/challenges \
  --state .ctfh-live/h4ckinggame --codex codex --secondary
```

`check-engine` checks local Codex login status without starting a model turn.
The native engine selection is saved; resume with
`rtk proxy ./ctfh2 live run --state .ctfh-live/h4ckinggame`.
Saved contests that previously selected the removed shift runner are migrated
to direct Codex while keeping their problem state and native thread IDs.

Use `live events --follow` in another terminal to watch public progress
messages, plan updates, tool starts/completions, checkpoints, and solve/stop
events while turns are running. Add `--challenge KEY` to follow one problem,
or `--json` for JSONL output. The latest 5,000 events survive restart.
Closing this viewer does not stop the contest. Events are collected by new
controller runs; earlier executions cannot be reconstructed from this log.
Tool arguments/output and private reasoning are excluded.

The `live` workflow accepts a site URL and account access, discovers the
challenge list, and starts all ready challenges concurrently by default.
It inspects each new site's actual page, rules, challenge navigation, solve
markers, and submission controls at run time; it has no preconfigured site
template or site-specific connector.
Each challenge has its own named Chrome session and persistent agent thread.
It stores challenge work, submissions, and resumable progress. A user-supplied
connection speaks the versioned `ctfh.turn.v1`
JSONL protocol. The user owns the Codex connection; the core can be prepared
and verified without model credentials.

Prepare a contest locally:

```bash
rtk proxy env PYTHONPATH=src python3 -m ctfh_core live init \
  https://h4ckingga.me/challenges --state .ctfh-live/h4ckinggame --account my-account
```

Run with your connection command (a JSON argv array, never evaluated by a shell):

```bash
rtk proxy env PYTHONPATH=src python3 -m ctfh_core live run \
  --state .ctfh-live/h4ckinggame \
  --agent-command '["/usr/bin/python3", "/absolute/path/my_codex_connection.py"]'
```

The command and thread ID are persisted. Resume with `live run --state ...`.
Use `live status --state ... --details` for checkpoints, `--resume-handoff`
after a user login handoff, or `--reset-thread` to reconnect while retaining
challenge work. Without a turn limit the controller keeps running; use a
user service for unattended operation.

Mark a problem you solved yourself with its key from `live status --details`:

```bash
rtk proxy env PYTHONPATH=src python3 -m ctfh_core live mark-solved \
  --state .ctfh-live/h4ckinggame --challenge stable-key
```

Only that problem's worker stops; other workers continue. The independent
site monitor also checks for problems solved directly on the site (every 15
seconds by default). Use `--scan-seconds` to adjust this interval and
`--max-workers` only when a concurrency cap is needed.

Browser verdicts require fresh, turn-bound receipts from `live browser`,
including the contest origin and observed URL. Candidates are journaled before
submission; unresolved candidates must be reconciled before another submission.
For an agent-accepted flag, the problem workspace must contain private
`flag.txt` (exact accepted flag bytes) and `writeup.md` before CTFH records the
accepted verdict. The flag file's SHA-256 must match the journaled candidate.
Existing problems solved outside the agent may have no recoverable flag file.
Workspaces, checkpoints, cooldowns, and fair problem selection survive restart.

See [the connection contract and operating guide](docs/LIVE_AGENT.md) for the
complete protocol, report fields, browser helpers, systemd command, and tests.
The built-in `codex exec` path uses the selected account's configuration and
persistent native sessions.
Actual account connectivity and model solve capability are separate validation items.

## Offline learning CLI

The package exposes:

```text
ctfh-learn verify-trace
ctfh-learn wiki-add
ctfh-learn wiki-show
ctfh-learn propose
ctfh-learn decide
```

Promotion input is a restricted evaluation-summary schema. Raw evaluator
secrets are not accepted as learning input.

## Validation status

As of 2026-10-06:

- full repository suite: **101 passed**
- real Codex supervised smoke: authoritative PASS
- final supervised attempt artifact and trace manifest published to CAS
- GPT-6.1 Sol final blocker recheck: **READY**, blockers none
- GPT-6 Astra final blocker recheck: **READY**, blockers none
- Claude Opus 5.5 participated in the architecture review that shaped V3; its
  final patch recheck could not be rerun because the local Claude CLI was
  logged out at final validation time

At the 2026-10-08 core delivery, before the shift integration,
`rtk pytest -q -o pythonpath=src` passed **140 tests**.
`tests/manual_live_core.py` also passed through a separate
JSONL fixture connection and real Chrome: two accepted challenges, one rejected
candidate, restart recovery after submission, zero replayed submissions, and
the alternate UI scoreboard. This verifies the contest core and connection
contract using a deterministic localhost fixture. The user owns the later
Codex connection and validation of actual model solve capability.
`tests/manual_live_parallel.py` passed with real Chrome: two simultaneous
problem workers, one site-submitted solution detected by the monitor, only its
worker stopped, and the second worker stopped after explicit user input.

The previous shift integration was removed after the user selected direct
Codex execution; see [the current verification record](docs/reviews/v3/FINAL_IMPLEMENTATION_STATUS.md).
The earlier 140-test result is historical. A prior full-suite rerun timed out
in an existing V0 browser cancellation test.

## Documents

- `docs/ARCHITECTURE.md` — preserved control-plane architecture and invariants
- `docs/ARCHITECTURE_V3_PROPOSAL.md` — V3 design and implementation sequence
- `docs/research/MODULAR_AGENT_ARCHITECTURE_2026-10-06.md` — modular
  agent/browser/skills/memory research synthesis
- `docs/research/BROWSER_NATIVE_CTF_SOLVER_2026-10-08.md` — browser-driven
  continuous solver research and architecture proposal
- `docs/reviews/v3/CONSENSUS.md` — multi-model architecture consensus
- `docs/reviews/v3/SYNTHESIS.md` — corrected V3 boundary synthesis
- `benchmarks/README.md` — matched evaluation rules

## Still deferred

These remain deferred until paired benchmark evidence justifies them:

- core-level generic Hand protocol
- global ToolRouter / ToolRegistry
- live shared memory and vector/graph retrieval on the solve path
- Desktop/GUI hand
- real-user-profile browser reuse in the isolated benchmark path (the live
  command uses the connected chrome-use profile)
- planner/executor overlays
- critic loops on the authoritative path
- heterogeneous model routing
- generic dynamic plugin discovery
- distributed scheduling
