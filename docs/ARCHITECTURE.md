# CTFH v2 — Performance-First Thin Supervisor

Status: architecture accepted after GPT-6 Sol, GPT-6 Astra, Opus 5.5 and literature review
Date: 2026-10-04

## 1. Objective

CTFH exists to maximize verified CTF points by the event deadline.

Priority order:
1. solve probability
2. wall-clock time to verified result
3. reliability and recoverability
4. cost / quota as an admission guardrail

CTFH is not a second agent framework. It is a thin supervisor around native
agents and prepared execution environments.

The first question for every new abstraction is:

> Does it measurably increase verified solves by the deadline or remove a
> demonstrated reliability bottleneck?

If not, it stays outside the hot path.

## 2. What research changed

The architecture is intentionally biased by measured results rather than by
generic agent-framework aesthetics.

Key findings:

- A 200-task NYU CTF Bench study found a prepared Kali environment solved
  104/200 tasks versus 85/200 with Ubuntu under matched settings (+9.5 pp).
  Environment and tool availability are first-class solver components.
- In the same study, Gemini Pro/Pro solved 52%, while Pro/Flash solved 28.5%,
  Flash/Flash 27%, and Flash/Pro 23.5%. This is evidence against assuming
  "strong planner + cheap executor" is automatically good. It is not a
  universal ban on heterogeneous models.
- EnIGMA shows usable interactive debugger/server/PTY interfaces materially
  improve CTF agents.
- D-CIPHER shows planning/fresh executors can help, but ablations are
  model-dependent. Auto-prompting can hurt, and weaker executors often reduce
  performance.
- A 2026 "second look" study found GPT-5 direct, Executor+Evaluator, and
  Planner+Executor+Evaluator all reached the same 19/30 peak coverage while
  added orchestration greatly increased wall-clock duration. More structure
  can improve step efficiency without improving competition speed.
- AgentCyberRange/CAGE benchmarks integrated model+harness systems, reinforcing
  that a native agent's context, tool semantics and execution harness belong in
  the measured configuration.
- CTFusion demonstrates that static benchmarks can be inflated by training
  contamination and search-time writeup/flag retrieval. Live or fresh holdouts
  are required for promotion decisions.

Therefore the benchmarked unit is the deployed runtime configuration, not a
model name.

## 3. Final top-level design

```
                         CTFH HOST
┌──────────────────────────────────────────────────────────────┐
│ KernelLoop (single authoritative event loop)                 │
│                                                              │
│  ├─ Scheduler / admission / deadlines / rate buckets         │
│  ├─ Supervisor (spawn, cancel, kill, reap)                   │
│  ├─ SQLite control store (single writer, WAL)                │
│  ├─ Artifact store (attempt-private -> sealed CAS)           │
│  ├─ External-effect journal (narrow scope only)              │
│  ├─ Orphan reaper / owner tags / host epoch                  │
│  └─ Metrics timestamps                                       │
│                                                              │
│          ┌──────────────┬──────────────┬──────────────┐       │
└──────────┼──────────────┼──────────────┼──────────────┼───────┘
           ▼              ▼              ▼              ▼
      TaskSource    TargetProvider  WorkerEnvironment  OutcomeAuthority
                                      │
                                      ▼
                                 AgentAdapter
                                      │
                                native agent
                            Codex / Claude / API
```

The kernel does not implement exploitation reasoning, a hidden planner, generic
tool reasoning, or model routing.

## 4. One kernel loop, one writer

v0 uses one kernel process per host.

At startup:
- acquire an exclusive process lock
- increment a persistent kernel epoch
- mark previously live attempts LOST
- sweep attempt-owned orphan resources
- reconcile only journaled external operations

The KernelLoop:
- owns all authoritative task/attempt/resource metadata
- receives a bounded event inbox
- applies short synchronous state transitions
- persists state in batches
- dispatches resulting commands only after commit

SQLite:
- WAL mode
- one writer owner
- read-only connections allowed for reports
- no transaction survives a model/tool/browser/network await
- large transcripts and tool output never flow through the DB

This is intentionally simpler than per-task coordinators with independent
writers.

## 5. Durable unit and identities

The durable unit is a Task/Challenge, not an agent conversation.

Identity types:

```
RunId
KernelEpoch
TaskId
TargetInstanceId
TargetEpoch
AttemptId
WorkerOwnerTag = (run_id, kernel_epoch, task_id, attempt_id)
TargetOwnerTag = (run_id, task_id, target_instance_id, target_epoch)
ProfileHash
```

A global "challenge revision" is not used to invalidate all concurrent results.

Freshness is dependency-based:
- an action against a target is valid only for the target instance/epoch it was
  created for
- evidence remains attributable even when another attempt learns new facts
- a final outcome is committed through a compare-and-swap from a nonterminal
  task state

## 6. State machines

### Task

```
OPEN -> SOLVED
OPEN -> CLOSED
```

### Attempt

```
QUEUED
  -> STARTING
  -> RUNNING
  -> FINISHED(completed | gave_up | error)

RUNNING/STARTING
  -> CANCELLING
  -> CANCELLED

STARTING/RUNNING/CANCELLING
  -> LOST        # kernel crash/restart
```

UNKNOWN is not an Attempt state.

### External operation

```
PENDING
  -> RUNNING
  -> SUCCEEDED | FAILED

RUNNING after timeout/crash
  -> RECONCILING
  -> SUCCEEDED | FAILED | UNKNOWN
```

UNKNOWN belongs only to external effects whose real-world outcome cannot be
determined.

Cleanup/leak status is separate from attempt outcome.

## 7. TaskSource

TaskSource returns frozen, content-addressed task specifications.

Responsibilities:
- task identity/version
- descriptions/points/category metadata when available
- challenge input artifact references
- target requirements
- evaluator identity
- deadline/event metadata

Examples later:
- local manifest
- CTFd
- DreamHack
- CTFusion/live CTF
- benchmark datasets

TaskSource does not own worker environments or agent processes.

## 8. TargetProvider and WorkerEnvironment are separate

This separation is intentional.

### TargetProvider

Owns the challenged system's lifecycle when CTFH controls it:
- provision/start
- health check
- endpoint discovery
- reset
- target epoch
- destroy

A target may be shared across sequential attempts or may be one-per-attempt,
depending on event rules.

A worker restart must not automatically reset a live target.

Target resources use TargetOwnerTag, not WorkerOwnerTag. They are never swept by
the attempt/environment reaper. On kernel restart the TargetProvider reconciles
the recorded target registry against reality and reports each target as usable,
lost, or stale. A controlled target reset increments TargetEpoch before new
attempt actions are admitted.

For a remote competition target that CTFH cannot control, TargetProvider is a
thin descriptor, health/reconciliation boundary, and epoch tracker.

### WorkerEnvironmentProvider

Owns the attacker's execution substrate:
- workspace
- filesystem isolation
- process namespace
- network policy
- installed security tools
- PTY/debugger processes
- optional browser capability
- hard kill
- cleanup
- orphan sweep by WorkerOwnerTag

Environment is disposable and normally private to one attempt.

Required lifecycle:

```
create(worker_owner, profile, cancel_token)
health_check(env)
kill(env)        # hard, confirmed process-tree stop
destroy(worker_owner)   # idempotent
sweep_workers(run_id, live_worker_owner_tags)
```

Every resource created during provisioning is tagged with WorkerOwnerTag before
it can escape provider control. A v0 provider must offer bounded/cancellable
provisioning plus a sweep-by-owner operation; otherwise it is not eligible for
supervised cancellation.

WorkerEnvironment sweep never touches TargetOwnerTag resources.

The environment is the v0 transaction boundary for ordinary local tool effects.
Local shell commands do not need a generic operation journal.

## 9. AgentAdapter

CTFH preserves native agent behavior instead of recreating every agent as a
lowest-common-denominator ReAct loop.

Examples:
- Codex CLI
- Claude Code
- future Qwen/Kimi/native coding agents
- custom API agent

The adapter understands lifecycle and observable output, not task reasoning.

Conceptual contract:

```
capabilities()
effective_configuration()
run(AttemptRequest, Env, AttemptSink, CancelToken) -> AttemptResult
```

Requirements:
- pin agent binary/version
- record effective model/reasoning/permissions/config
- fail if required capabilities are silently unavailable
- report observable usage/latency when exposed
- declare usage visibility: none | aggregate | incremental
- declare budget control: none | attempt_cap | request_level
- declare rate control: none | launch_only | request_level
- declare trace coverage: native tools may hide raw tool output
- never get a DB handle
- write bulk trace/artifacts only to attempt-private output

The kernel owns the environment lifetime. The adapter cannot keep a process alive
after the environment has been killed.

Custom API agents may internally use a ModelBackend/LiteLLM adapter, but
ModelBackend is not a kernel contract.

## 10. Attempt output and artifacts

Each attempt gets a private directory.

```
attempts/<attempt_id>/
  trace.jsonl
  stdout/
  artifacts/
  candidate.json
  metrics.json
```

Bulk data goes directly to append-only files, not through the kernel inbox.

At attempt end:
1. stop/confirm process tree
2. seal attempt directory
3. hash immutable artifacts
4. atomically promote relevant artifacts to content-addressed storage
5. record references in SQLite

A partial/unsealed artifact is never authoritative evidence.

Native-agent compaction may mean true raw tool output is unavailable. CTFH must
record that limitation rather than label transformed output "raw".

## 11. OutcomeAuthority

Worker "solved" text is never authoritative.

OutcomeAuthority declares:
- authority: authoritative | advisory
- effect: local | external
- verification protocol/version

Examples:
- local benchmark oracle: authoritative + local
- live scoreboard submission: authoritative + external
- local flag-pattern check: advisory + local

Only an authoritative PASS can commit Task -> SOLVED and cancel sibling attempts.

For external verification/submission:
- persist intent before dispatch
- serialize candidate hash per task
- distinguish accepted/rejected/unknown delivery
- never blindly retry UNKNOWN unless remote idempotency or a reconcile probe is
  available

This replaces separate generic Verifier + Submitter abstractions in the hot path.

## 12. External-effect journal: narrow scope

Journal only effects that:
1. leave the disposable worker environment, and
2. are non-idempotent, quota-limited, or operationally consequential.

Examples:
- live scoreboard submission
- start/reset competition instance
- authenticated external form submission
- remote upload/resource creation

Do not journal:
- grep
- local shell
- gdb steps
- files written inside disposable attempt workspace
- ordinary reads

Each journaled operation type must provide at least one:
- remote idempotency key
- reconcile() probe
- explicit acknowledgement that UNKNOWN is an acceptable final automation state

## 13. Cancellation and cleanup

Cancellation is bounded process supervision.

Attempt cancellation:
1. commit CANCELLING
2. trip CancelToken / ask adapter or in-flight environment provisioning to stop
3. short cooperative grace period
4. if the attempt was STARTING, wait for the environment-create operation to
   become terminal; if it returns an Env after cancellation, immediately kill it
5. run the provider's owner-scoped sweep and confirm no WorkerOwnerTag resource
   can still appear or remain
6. hard-kill any established WorkerEnvironment process tree
7. confirm the process tree is dead
8. only then mark CANCELLED and release the WorkerEnvironment capacity reservation
9. slower non-capacity cleanup may continue asynchronously
10. teardown failures go to a leak list/reaper

A STARTING attempt can never become CANCELLED while its create task can still
publish resources. Providers that cannot provide this bounded create/sweep
barrier are rejected by the v0 supervised environment contract.

Attempt completion does not wait for slow cleanup after the execution resources
needed for admission have been safely reclaimed.

Journaled external operations already dispatched are reconciled independently.

A late verified candidate may still be recorded, but a dead attempt cannot regain
permission to act on a replaced target epoch.

## 14. Ownership and fencing

v0 does not build a universal lease subsystem.

Primary worker isolation is:
- one WorkerEnvironment per attempt
- private workspace
- WorkerOwnerTag on every worker-created resource
- kernel epoch on restart
- kill old holder before exclusive worker-resource reassignment
- startup worker-resource orphan sweep

Target resources are owned separately by TargetOwnerTag and reconciled only by
TargetProvider. Attempt cancellation and WorkerEnvironment sweep cannot destroy a
still-valid target.

A database generation number cannot revoke an unmediated browser socket or
process handle, so v0 does not pretend it can.

Later shared resources (authenticated browser profile, shared competition
account, scarce external instance) may get specific brokers/leases where the
I/O boundary can actually enforce fencing.

## 15. Strategy

Strategy is pure and replayable:

```
decide(TaskView) -> Sequence[Command]
```

No I/O.
No async.
No hidden model call.
Current time/deadline is passed in TaskView.

v0 has only DirectStrategy.

Later commands may include:
- StartAttempt(profile_variant)
- CancelAttempt(id, reason)
- Verify(candidate)
- Finish(outcome)

Strategies are benchmark features, not assumptions.

## 16. Scheduler

Scheduler is part of the kernel in v0.

v0 resources:
- attempt slots
- WorkerEnvironment slots
- AgentAdapter launch/concurrency reservations
- event/task deadlines
- only quotas/budgets that the selected AgentAdapter can actually enforce

For a native agent whose internal model calls are opaque, CTFH does **not** claim
request-level token/rate enforcement. It may cap attempt concurrency and deadline
and record aggregate usage retrospectively when exposed.

A profile requesting a hard token/dollar/request cap is rejected unless the
AgentAdapter declares a matching enforceable budget/rate-control capability.
Request/token buckets are used only when CTFH or the adapter mediates those
requests.

Admission is all-or-nothing so an attempt never holds one resource while waiting
for another.

v0 uses a simple deadline-aware priority/slot policy.

Do not ship an expected-value optimizer before there is enough trace data.

Competition optimization is eventually:
- maximize marginal verified points by event deadline
- model queue/setup/rate-limit opportunity costs explicitly

Not simply "points / seconds" for each isolated task.

## 17. HarnessProfile and RunLock

HarnessProfile is a deployable configuration label.

At admission it resolves to an immutable RunLock with hashes/versions for:
- AgentAdapter binary/config
- effective model + reasoning parameters
- WorkerEnvironment image/template
- target provider version
- tool packages
- prompts
- skills
- native context settings
- optional outer context view
- strategy parameters
- browser capability/config
- deadline/concurrency settings
- CTFH version

The experimental unit is:

```
task instance
x frozen RunLock
x evaluation protocol
x repetition
```

HarnessProfile alone is not sufficient to attribute causality.

## 18. Browser

Browser is a WorkerEnvironment capability, not a mandatory global path.

For many tasks:
- direct HTTP
- shell tools
- source analysis
are faster than browser interaction.

When browser state is required:
- use an explicit browser capability/profile
- browser lifecycle has exactly one owner
- authenticated state is never silently migrated between implementations

Expected policy modules:
- GhostChrome for managed persistent browser workflows
- chrome-use for the user's real authenticated browser when required
- compatibility browser only when explicitly configured

The old rule "GhostChrome first for every challenge action" is removed.
The correct rule is "use the cheapest sufficient capability; if browser is
needed, use the configured owner."

Future Jev:
- decision overlay only
- consumes indexed observation
- chooses bounded operation/target
- does not launch a second browser lifecycle by default

## 19. Context and token optimization

Do not stack context managers blindly.

Native Codex/Claude context behavior is part of RunLock.

Outer context optimizers are conditional experimental features.

Rules:
- capture available evidence before transformation
- transformed output records provenance/omissions
- never call transformed output raw
- context optimization never authorizes execution

Candidate integrations later:
- RTK: explicit terminal-output compaction view only
- context-mode: very large observation sandbox/index
- codebase-memory: large source-tree structural retrieval

Important RTK rule:
- no security-critical reliance on rewrite/auto-allow hooks
- kernel/environment sandbox owns permissions

A context feature ships only if paired trials improve verified completion,
deadline performance, or prevent overflow without harming success.

## 20. Skills

Use Agent Skills format for portable domain knowledge:
- SKILL.md
- scripts/
- references/
- assets/

Use progressive disclosure:
- metadata at startup
- body on activation
- supporting resources on demand

Skills are not v0 kernel plugins.
They become part of AgentAdapter/profile configuration and are hashed into
RunLock.

Avoid task-ID-specific solutions.
Benchmark hidden/live tasks to test generalization.

## 21. Plugin loading

v0 does not build a package plugin ecosystem.

Use explicit factory maps/configuration for the small number of real adapters.

When two or more independent third-party implementations create real replacement
pressure:
- define a Protocol from observed needs
- add conformance tests
- then add Python entry-point discovery

No generic event bus on the hot path.

MCP is useful at external capability boundaries when interoperability matters;
kernel-internal calls remain direct.

## 22. Benchmark methodology

Static benchmarks are useful for regression but not enough for promotion.

Required controls:
- fresh/live/private holdouts where possible
- clear separation between evaluator secrets and worker environment
- isolated workspaces
- isolated native memory/context indexes
- no prior browser history
- no previous trajectories injected
- record web-search availability
- audit successful traces for retrieved known flags/writeups

CTFusion evidence:
- static NYU results materially exceeded live-CTF results
- web-search augmented agents retrieved known solutions/flags
- use live or leakage-resistant evaluation for final promotion

Report:
- verified completion fraction over elapsed time
- completion fraction at event/task deadline
- time to first authoritative result
- queue/setup/agent/verify/cleanup spans
- failures/timeouts in denominator
- infrastructure failures separately
- cleanup leaks
- paired repetitions and uncertainty

Do not report solved-only latency as performance.

## 23. v0: real vertical slice

v0 is complete only when a real pinned native agent repeatedly solves/scored
tasks through the wrapper.

v0 includes:

### Kernel
- host lock + kernel epoch
- single KernelLoop
- SQLite run ledger
- bounded inbox
- simple scheduler with slots/deadlines and adapter-declared enforceable quotas
- process Supervisor
- ArtifactStore
- orphan reaper
- minimal external-operation protocol

### Boundaries
- LocalTaskSource
- LocalTargetProvider
- LocalWorkerEnvironment
- one real AgentAdapter (Codex CLI first)
- DirectStrategy
- LocalOutcomeAuthority

### Required measurements
- queue wait
- environment preparation
- agent start
- time to first candidate
- total agent duration
- verification
- cleanup
- end-to-end

### Required fault tests
- native agent crash
- ignored cooperative cancellation
- kernel kill/restart
- orphan process cleanup
- partial artifact
- stale target epoch
- ambiguous external operation fixture
- output flood/backpressure

### Required performance gate

Use a **matched A/B baseline**. The two arms must use the same:

- exact task instances and input artifact hashes
- target instance/reset policy and initial TargetEpoch
- WorkerEnvironment template/image and installed tool versions
- CPU/RAM/network limits
- native agent binary/version
- model ID, reasoning settings and provider/account class
- agent prompts, rules, skills and native context configuration
- web-search availability
- browser capability/profile policy
- permissions/sandbox policy
- task deadline, model budget/quota envelope and retry policy
- OutcomeAuthority/evaluation protocol

Arm A — **direct native**:
- launch the pinned native agent directly in the matched prepared environment
- no CTFH lifecycle supervisor around the agent

Arm B — **CTFH supervised**:
- launch the exact same pinned native agent in the matched prepared environment
- CTFH adds only the lifecycle/isolation/measurement path being evaluated

Both arms write an effective-configuration manifest and hash before the run.
If the hashes differ outside an explicitly declared treatment field, the pair is
invalid for wrapper-overhead attribution.

Primary comparison:
- authoritative completion
- elapsed time to authoritative completion
- completion by the same deadline
- infrastructure/recovery failures

CTFH must account for wrapper overhead under these matched conditions.

No fake-only milestone counts as v0 completion.

## 24. v1

After v0 produces trustworthy baseline data:

- Kali/Docker WorkerEnvironment
- environment warm pool / copy-on-write templates
- PTY/debugger support
- live CTF/CTFd/DreamHack TaskSource/TargetProvider as needed
- authoritative scoreboard path
- GhostChrome browser capability
- second native AgentAdapter (Claude Code)
- explicit RTK output view
- profile lockfile and benchmark runner
- CTFusion/live holdout support

## 25. v2

Only after measured gains:

- pass@k
- hedged pass@k:
  start one attempt, launch siblings only after historical p50 success time if
  still unsolved
- sibling cancellation after authoritative success
- Agent Skills packs
- conditional context-mode
- profile tournament
- replay simulator for scheduling policies

Pass@k is evaluated under shared event-level quotas, not free extra compute.

## 26. v3

Only when benchmark evidence justifies each item:

- planner/executor
- parallel heterogeneous hypotheses
- Jev browser decision overlay
- codebase-memory
- learned profile routing
- broader plugin entry-point ecosystem
- distributed scheduler if one host is actually insufficient

## 27. Non-goals

Not in the foundation:
- a new generic ReAct framework
- mandatory planner/executor
- universal model router
- universal lease abstraction
- Redis
- vector DB
- LangGraph/Temporal dependency
- Playwright as required browser owner
- mandatory RTK/context-mode/codebase-memory
- automatic model promotion from vendor benchmarks

## 28. Core invariants

These become executable tests.

I1. Only KernelLoop writes authoritative control state.
I2. Commands are dispatched only after their causal state transition commits.
I3. No transaction spans an await/external wait.
I4. Every attempt environment is attributable to a WorkerOwnerTag and sweepable; target resources use TargetOwnerTag and are outside the attempt reaper.
I5. An attempt is CANCELLED only after environment provisioning is terminal, owner-scoped sweep is clean, and any established process tree is confirmed dead.
I6. Only authoritative outcome can transition Task -> SOLVED.
I7. Journaled external effect is durably RUNNING before dispatch.
I8. UNKNOWN external effects are never blindly retried.
I9. Bulk output cannot block the kernel inbox or DB writer.
I10. Strategy is pure/replayable.
I11. A target reset increments TargetEpoch and invalidates later actions from old epochs.
I12. RunLock is immutable for an admitted attempt.
I13. Evaluator-only secrets are not mounted into worker environments.
I14. v0 is not accepted until a real native agent passes the end-to-end gate.

## 29. Primary performance hypothesis

The first performance hypothesis to test is intentionally simple:

> A strong native agent in a ready, well-tooled, isolated environment with a thin
> supervisor and authoritative verification will beat a more elaborate orchestration
> layer on score-by-deadline until data proves otherwise.

Every later module must beat this baseline in paired evaluation.
