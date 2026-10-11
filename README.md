# ctfh-core

**English** · [한국어](README.ko.md)

A terminal CTF runner that opens one interactive Codex session per challenge, with a separate checker for flag reproduction, website submission, and write-up review.

Give `ctfh2` a contest URL. It discovers challenges through your connected Chrome, downloads attachments, opens solver windows in tmux, and tracks their progress. You can enter any solver window and work with Codex directly.

```text
Contest URL → Chrome discovery → challenge folders → Codex windows
                                                         ↓
                                    checker → reproduce → submit → review
```

This guide covers the default **native runner** (`ctfh2 <URL>`). The older `start` / `live` controller and the isolated benchmark runtime are separate workflows; see [Advanced workflows](#advanced-workflows).

## Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Start your first contest](#start-your-first-contest)
- [Use the dashboard](#use-the-dashboard)
- [Detach, stop, and resume](#detach-stop-and-resume)
- [Commands and configuration](#commands-and-configuration)
- [Files and verification](#files-and-verification)
- [Troubleshooting](#troubleshooting)
- [Advanced workflows](#advanced-workflows)
- [Development](#development)

## Requirements

| Component | What you need |
|---|---|
| OS | Linux is the current development environment. Memory controls use `/proc` and systemd user scopes. Other platforms have not been validated. |
| Python | **3.12 or newer**, with `venv` and pip. |
| tmux | Installed and available on `PATH`. |
| Codex CLI | Installed, authenticated, and able to use your chosen model. The native runner requires `--no-daemon`, `--dangerously-bypass-approvals-and-sandbox`, and `codex exec --json --output-schema`. |
| Chrome + chrome-use | An open Chrome profile connected to [chrome-use](https://github.com/leeguooooo/chrome-use), with access to the contest site. |
| Model access | Solvers and collection, download, submission, and review jobs consume your Codex account's usage. |

The native runner launches Codex with **host access and approval/sandbox bypass enabled**, including its background Codex jobs. It can execute challenge code and access files available to your user. The `dangerously_bypass_approvals_and_sandbox = false` value in `ctfh.toml` is **not a sandbox switch for this runner**. Use an environment suitable for running CTF code, only solve authorized challenges, and follow the contest's automation/AI rules.

RTK is optional for running CTFH. The commands below work without it.

## Installation

### 1. Install the project

On Ubuntu/Debian with Python 3.12+ available:

```bash
sudo apt update
sudo apt install git python3 python3-venv python3-pip tmux

git clone https://github.com/yc54616/ctfh-core.git
cd ctfh-core
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Use an editable installation (`-e .`): the launcher reads `ctfh.toml` and bundled fixtures from this checkout. Installation provides `ctfh2` and `ctfh-learn`. The `./ctfh2` shortcut also works from the repository root once dependencies are installed. Activate the virtual environment again in new terminals.

### 2. Prepare Codex and Chrome

1. Install and authenticate [Codex CLI](https://github.com/openai/codex). Confirm `codex login status` succeeds and that its help lists the required flags above.
2. Follow the [chrome-use installation guide](https://chrome-use.leeguoo.com/en/install.html): install the CLI, add its Chrome extension, and register the local bridge with `chrome-use extension install`.
3. Keep the connected Chrome profile open and log in to the CTF site. Make chrome-use available to Codex; its bundled usage guide is available with `chrome-use skills get core`.

Check the prerequisites without starting a contest:

```bash
python --version
tmux -V
codex login status
chrome-use status
ctfh2 --help
ctfh2 go --help
```

Pip installs Python dependencies. Codex, tmux, Chrome, chrome-use, and challenge-specific tools such as debuggers are installed separately.

## Start your first contest

Replace `https://ctf.example.com/challenges` with the real URL. For a first run, limit concurrency to two solver windows and disable automatic submission:

```bash
ctfh2 https://ctf.example.com/challenges \
  --dir "$HOME/CTF/my-contest" \
  --max-parallel 2 \
  --no-submit
```

If your account cannot use the default model, add `--model YOUR_MODEL --rescan-model same`. To choose a supported reasoning level, add an option such as `--thinking high`.

On startup:

1. CTFH prints the contest directory. On a terminal it optionally asks for site credentials as a re-login fallback. **Press Enter at the username prompt to skip saving credentials** and use the existing Chrome login.
2. A Codex collector reads the site, rules, and challenge list. Initial collection can take a few minutes. If login needs your help, finish it in Chrome and press Enter when prompted.
3. CTFH creates a tmux session. For the example directory its name is `ctfh-my-contest`. The `checker` window shows progress; other windows run interactive Codex solvers.
4. Attachments download in the background. Each challenge becomes eligible to start when its files are ready. After three failed download attempts, it may start with incomplete files.
5. The checker reproduces candidate flags with `solve.py` and reviews `writeup.md`. With `--no-submit`, it does not submit flags to the website.

For normal operation with automatic submission, omit `--no-submit`:

```bash
ctfh2 https://ctf.example.com/challenges --max-parallel 2
```

**Defaults:** automatic submission is on; `--max-parallel 0` means all eligible challenges can run concurrently. Reattaching to an existing contest does not change its running checker's options; use the restart procedure below.

## Use the dashboard

The `checker` window shows each challenge's state, token usage, activity, and reason for waiting. Dashboard labels and generated agent instructions are currently primarily Korean; this README does not change the UI language.

Select a row with the arrow keys or mouse:

| Key | Action |
|---|---|
| `Enter` | Enter the selected solver's Codex window. |
| `n` | Nudge the solver now. Manual nudges do not count against the automatic limit. |
| `r` | Reopen a closed window or resume a `manual` / `hold` challenge. |
| `o` | Read the challenge's `work/NOTES.md`. |
| `p` | Hold the challenge and close its solver window. Resume with `r`. |
| `d` | Mark a challenge solved by you/a teammate and close its solver window. |
| `s` | Rescan the site now. |
| `t` | Sort by the next column; clicking a header also changes sorting. |
| `+` / `-` | Adjust the solver-window limit. This override survives checker restarts unless the configured `--max-parallel` value changes. |
| `q` / `Ctrl-c` | Quit the checker. **Solver windows continue running.** |

These are dashboard shortcuts. Inside a solver window, interact with Codex normally.

| tmux shortcut (default prefix) | Action |
|---|---|
| `Ctrl-b`, then `w` | List windows; choose `checker` to return to the dashboard. |
| `Ctrl-b`, then `n` / `p` | Next / previous window. |
| `Ctrl-b`, then `d` | Detach while the contest keeps running. |

## Detach, stop, and resume

**Leave it running:** detach with `Ctrl-b`, then `d`. In another terminal with the virtual environment active:

```bash
ctfh2 status --dir "$HOME/CTF/my-contest"
ctfh2 --dir "$HOME/CTF/my-contest"
```

With no arguments, `ctfh2` selects the contest containing your current directory, or the last started contest recorded in `~/.cache/ctfh/last_contest`. On a first run with no saved contest, provide a URL. Use `--dir` to select a specific contest reliably.

**Pause one challenge:** select it and press `p`. Its `hold` state survives restart; press `r` to resume.

**Change checker options:** quit the checker with `q`, wait for its window to close, then launch again with the same `--dir` and desired options. Existing solver windows retain their model/settings; new windows use the new options.

**End the contest's tmux session:** from another terminal, confirm its name and terminate that session:

```bash
tmux list-sessions
# This name matches the --dir "$HOME/CTF/my-contest" example.
tmux kill-session -t ctfh-my-contest
```

This closes its checker and solver windows. Saved challenge files remain. Separately detached processes started by challenge tools may need their own cleanup.

Restart from saved files, for example to enable automatic submission after the initial trial:

```bash
ctfh2 --dir "$HOME/CTF/my-contest" --max-parallel 2
```

Launching an existing tmux session only reattaches. Different flags do not replace a running checker's options. Supply your desired options again on restart or save them in `ctfh.toml`.

## Commands and configuration

### Everyday commands

```bash
ctfh2 https://ctf.example.com/challenges     # Start a contest.
ctfh2                                       # Reattach/resume the current or last contest.
ctfh2 status                                # Show saved progress.
ctfh2 done web-1 pwn-2                      # Mark these challenges solved by you/a teammate.
ctfh2 go --help                              # All native-runner options.
ctfh2 checker --help                         # Checker options.
```

`web-1` and `pwn-2` are examples: use the actual folder names under `challenges/`. `status`, `done`, and `approve` accept `--dir`.

`ctfh2 approve web-1` is an override: it trusts the current `flag.txt`, bypasses reproduction, and queues it for the running checker to submit. It can authorize a previously tried flag again. It **does not submit synchronously**, and a checker started with `--no-submit` still will not submit. Use it only after independently checking the flag.

### Main options

| Option | Default | Meaning |
|---|---|---|
| `--dir PATH` | `~/Desktop/CTF/<site-slug>/` for a new URL | Contest workspace. |
| `--max-parallel N` | `0` | Solver-window cap; `0` means no fixed cap. Background jobs are separate. |
| `--no-submit` | Not set | Disable website submission; keep verification and write-up review. |
| `--model MODEL` | `gpt-6-sol` | Solver and initial-collection model. |
| `--thinking LEVEL` | Account default | Reasoning effort; alias: `--reasoning-effort`. Model support varies. |
| `--rescan-model MODEL` | `gpt-6-luna` | Periodic rescan and attachment-download model; `same` uses `--model`. |
| `--rescan-thinking LEVEL` | `low` | Rescan/download reasoning; `same` follows `--thinking`. |
| `--submit-thinking LEVEL` | `low` | Submission reasoning, on the main model. |
| `--review-thinking LEVEL` | `medium` | Write-up review reasoning, on the main model. |
| `--rescan-minutes N` | `60` | Full discovery interval; `0` disables periodic full rescans. Other checker checks continue. |
| `--idle-seconds N` | `180` | Wait before nudging an idle solver. |
| `--nudge-limit N` | `3` | Automatic nudge/restart limit before handing control to you. |
| `--solve-timeout N` | `300` | Seconds allowed for a `solve.py` reproduction. |
| `--memory-max SIZE` | `auto` | Shared solver memory cap via systemd; `auto` is 98% of RAM. Examples: `8G`, `off`. Not a per-window cap. |
| `--plain` | Not set | Text board instead of the interactive dashboard. |
| `--no-attach` | Not set | Create the tmux session without attaching. |
| `--secondary` | Not set | Use an existing `codex-secondary` executable. CTFH does not install this account wrapper. |

The default account uses `codex` and `~/.codex`. `--secondary` is a CTFH option, not a direct Codex argument. Claude is supported by the isolated smoke/benchmark adapter, not by this native contest launcher.

### Save defaults

Edit **this checkout's** [ctfh.toml](ctfh.toml). Command-line values take precedence. Put these keys under `[native]`, **before** `[native.secondary]`:

```toml
[native]
binary = "codex"
model = "gpt-6-sol"
thinking = "high"
max_parallel = 2
submit = false
rescan_model = "same"
rescan_thinking = "low"
submit_thinking = "low"
review_thinking = "medium"
rescan_minutes = 60
idle_seconds = 180
nudge_limit = 3
solve_timeout = 300
memory_max = "8G"
plain = false
```

This is a conservative example, not the shipped defaults. Set `submit = true` to enable automatic submission if the file disabled it; there is no `--submit` flag to override `submit = false`. Restart the checker to apply changes. A contest directory's `contest.toml` describes that contest; it is not the launcher defaults file.

## Files and verification

```text
my-contest/
├── contest.toml                 # Site URL and contest overrides
├── AGENTS.md                    # Contest instructions
├── .ctfh/                       # Collection logs, site metadata, board, checker state
└── challenges/
    └── web-1/
        ├── challenge.md         # Description
        ├── challenge.json       # Site metadata
        ├── AGENTS.md            # Solver instructions
        ├── files/               # Attachments
        ├── work/NOTES.md         # Progress and recovery notes
        ├── solve.py             # Reproducible solver
        ├── flag.txt             # Candidate/accepted flag
        ├── writeup.md           # Explanation of the solve
        ├── status.json          # Checker-owned state
        └── .ctfh-check/          # Verification and review evidence
```

Files appear as their stages run. Remote challenges may also have `remote.txt`. Keep contest directories and credentials out of your public source repository.

The usual path is `solving → checking → verified → submitting → accepted → done`. `accepted` means the website accepted the flag; `done` also requires write-up review. With `--no-submit`, a verified solution can reach `done` after review **without website acceptance**.

The checker reruns `solve.py` and compares its output with `flag.txt`. For remote challenges it rejects known placeholders and flags copied from attachments; a newly reproduced remote flag may replace an older one. A wrong flag hash is not automatically resubmitted. Unclear submission outcomes can retry up to three times, two minutes apart, then remain `unknown` for you to inspect.

Reproduction alone does not prove website acceptance: check the recorded submission result. Challenges you mark with `done`, or that the site already reports solved, do not require invented local flag files.

### Login persistence

CTFH first uses the connected Chrome login, then tries Chrome's saved-password autofill if the session expires. The optional startup prompt saves a fallback to `~/.config/ctfh/credentials.toml` with mode `600`. This contains **plaintext credentials**, not an encrypted vault. `secret-fill` supplies values through `chrome-use fill --from-env` without printing them. Full-access Codex processes still have your user's file access. Complete 2FA and captchas yourself in Chrome.

When a challenge service needs an account, the solver signs up by itself with the Google account of Chrome's default profile (`chrome-use browsers`, DEFAULT row): that email, its local part as the username, and a fresh random password kept in `work/ACCOUNTS.md`. It may use "Sign in with Google" and open the verification mail in Gmail. Steps only a person can do (captcha, SMS, payment) go to the challenge's `needs_user.txt`; the board shows the challenge as `입력 필요` and the checker stops nudging it until the file is gone.

## Troubleshooting

| Symptom | What to check or do |
|---|---|
| Command not found / missing `yaml` or `textual` | Activate `.venv` and run `python -m pip install -e .` from the checkout. |
| No contest found | Supply a URL on the first run or select saved work with `--dir`. |
| Chrome disconnected / login needed | Check `chrome-use status`, extension, open profile, and site login. Complete the launch prompt or rescan with `s`. |
| Unsupported model/reasoning | Choose an available `--model`; use `--rescan-model same` if the default rescan model is unavailable. |
| Unsupported `--no-daemon` or another Codex option | Check `codex --help` and `codex exec --help`; the native launcher requires a compatible CLI build. |
| `manual` / 직접 확인 | Nudge limit reached or two nudges made no note progress. Read `work/NOTES.md`, enter the window, or use `r` / `n`. |
| `입력 필요` | The site login was lost before a submit (retried every 5 minutes), or a solver wrote `needs_user.txt`. Do the step in the reason column in Chrome; the solver deletes the file and continues. |
| `hold` / 보류 | Resume with `r`. Contest end also puts unfinished challenges on hold. |
| Waiting for attachments | Check `.ctfh/` download logs and links. Solving may start with incomplete files after three failures. |
| Waiting for a closed server | Wait for the remote service to recover; read the notes. Local success does not establish a remote solve. |
| `unknown` submission result | Inspect the scoreboard and `.ctfh-check/` evidence before using `approve`. |
| Account quota/outage | The checker pauses nudges, restarts, and rescans and probes periodically. Restore account access; reasoning settings do not bypass provider policy. |
| systemd user-scope error | Use a working Linux user systemd session or `--memory-max off` to disable that memory cap. |
| New settings ignored on resume | Restart the checker. Also check `[native]` vs `[native.secondary]` in TOML. |

## Advanced workflows

| Workflow | Entry point | Guide |
|---|---|---|
| Native contest runner (default) | `ctfh2 <URL>` | This README; [design notes](docs/research/NATIVE_CODEX_SOLVER_CHECKER_2026-10-09.md) |
| Earlier live controller | `ctfh2 start <URL>` / `ctfh2 live run` | [Live protocol and operating guide](docs/LIVE_AGENT.md) |
| Isolated direct/supervised benchmark | `ctfh2 smoke` | [Architecture](docs/ARCHITECTURE.md), [V3 design](docs/ARCHITECTURE_V3_PROPOSAL.md), [benchmark rules](benchmarks/README.md) |
| Offline evidence/knowledge tools | `ctfh-learn --help` | `verify-trace`, `wiki-add`, `wiki-show`, `propose`, `decide` |

Resume the `start` workflow with `ctfh2 start`; its state and shortcuts differ from the native runner. The [sample dashboard image](docs/images/ctfh-terminal-dashboard.svg) depicts that older controller, not the default native dashboard.

The benchmark runtime retains Codex/Claude adapters, Bubblewrap isolation, optional PTY/GhostChrome services, pinned RunLock configurations, sealed traces, and read-only skills/knowledge. Offline learning is a separate `ctfh_learning` package. These benchmark boundaries do not sandbox the native runner.

A smoke run needs additional runtime tools and a working model account; it starts real model turns:

```bash
ctfh2 smoke --agent codex --mode pair --model gpt-6-sol --effort high
ctfh2 smoke --help
ctfh-learn --help
```

Historical validation and limitations are in [project decisions](docs/DECISIONS.md) and [dated implementation records](docs/reviews/v3/FINAL_IMPLEMENTATION_STATUS.md).

## Development

From the activated virtual environment:

```bash
python -m pip install pytest
python -m pytest -q -o pythonpath=src
git diff --check
```

The automated suite covers the native checker, live-controller state, artifacts, and learning invariants. Separate `tests/manual_*.py` scripts require additional browser/runtime setup and are not included above. Local test success does not establish real-contest solve performance or current model/account availability.
