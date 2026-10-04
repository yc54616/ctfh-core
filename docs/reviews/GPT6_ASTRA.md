**Verdict: substantial revision required.** The draft prioritizes replaceability before establishing performance, and several correctness guarantees exceed what its proposed boundaries can enforce. Its largest contradiction is [v0](‌/home/yc54616/workspace/ctfh-v2-review/ARCHITECTURE_DRAFT.md:504): fake workers validate framework machinery while real agents, environments, and performance measurement wait until v1.

I can review reliability, isolation, and evaluation design, but not provide a build-ready orchestration blueprint for autonomous exploitation. The corrections below stay at that systems-review level. No files were edited.

**Evidence that should change the design**

- **Environment effects are substantial, but configuration-specific.** The April 2026 study’s matched Gemini 3 Pro comparison was **52.0% versus 42.5%** for Kali versus Ubuntu across 200 tasks. Its role-pairing results were Pro/Pro **52%**, Pro/Flash **28.5%**, Flash/Flash **27%**, and Flash/Pro **23.5%**. These were single-trial measurements within D-CIPHER, not proof about every model combination or native harness. The evidence undermines “strong planner + cheap executor” as a default; it does not establish universal superiority of homogeneous teams. [Primary study](https://arxiv.org/html/2604.17159)
- **The September 17, 2026 harness study argues against unconditional middleware.** Across 176 settings, context management mainly helped avoid overflow; recoverable elision added machinery without accuracy gains. Planning and predefined tools had different effects by model and task. These are coding-benchmark findings, so transfer to CTF evaluation remains a hypothesis. [Primary study](https://arxiv.org/abs/2609.20804)
- **Benchmark deployed configurations, then isolate causes.** Harness-Bench found substantial configuration-level variation, while reporting its model-bound Codex reference separately from configurable harnesses. AgentCyberRange similarly evaluates integrated systems. Neither licenses attributing every configuration difference to the model—or to the harness alone. [Harness-Bench](https://arxiv.org/html/2605.27922), [AgentCyberRange](https://arxiv.org/html/2606.14295)

**KEEP**

- One authoritative owner of durable state; workers propose rather than declare completion.
- Native agent integration without forcing every agent through a common chat API.
- Environment identity as part of an experiment’s identity.
- Independent outcome verification and immutable evidence.
- Explicit uncertainty after ambiguous external effects.
- Short database transactions with no model, browser, or network waits.
- Conditional activation of expensive context systems.
- Explicit promotion based on measured results.

These are useful constraints. They do not require twelve interchangeable module types.

**DELETE / DEFER**

| Item | Why it should leave v0 |
|---|---|
| Generic `PluginRegistry` and package-entry-point ecosystem | No demonstrated replacement pressure; creates compatibility work before baseline results. |
| Six initial strategy modules | Each multiplies experiment combinations and failure attribution problems. |
| General-purpose lease framework | Resource-specific ownership must work before a common lease abstraction can mean anything. |
| Separate `BrowserDecisionPolicy` | Adds another decision and translation boundary before browser reliability is established. |
| Generic assertion-merging engine | “Merge proposed facts” hides difficult validity rules behind an innocent name. |
| Automatic profile selection | Sparse, confounded category statistics cannot justify a reliable router. |
| Mandatory indexing or summarization | Can increase latency, lose evidence, and duplicate native context management. |
| “Zero kernel changes” as a success criterion | Rewards premature interfaces rather than measured execution quality. |

Keep fake adapters for fault injection. Delete the assumption that a fake-only milestone validates the architecture’s performance premise.

**CHANGE**

1. **Separate the worker environment from the task environment.**  
   [EnvironmentProvider](‌/home/yc54616/workspace/ctfh-v2-review/ARCHITECTURE_DRAFT.md:75) mixes workspace, process ownership, remote instances, and reset authority. Their lifetimes and sharing rules differ. A worker restart must not implicitly reset shared task state.

2. **Narrow the kernel’s claims to boundaries it controls.**  
   A native agent can issue shell, browser, and network operations internally. An outer `AgentAdapter` cannot promise to journal every effect merely because a `ToolAdapter` interface exists. Declare visibility and enforcement gaps explicitly.

3. **Replace global revision freshness with dependency validity.**  
   At [line 180](‌/home/yc54616/workspace/ctfh-v2-review/ARCHITECTURE_DRAFT.md:180), an attempt starts against `base_revision`. An unrelated metadata update should not invalidate its evidence. Conversely, unchanged challenge metadata does not make evidence valid after the underlying environment changes.

4. **Separate execution outcome, verification, and cleanup.**  
   `SUCCEEDED`, `CANCELLED`, and `UNKNOWN` currently mix different questions. A worker can stop while an external request remains unresolved. A result can be independently verified while cleanup is incomplete.

5. **Correct the completion flow.**  
   In [line 432](‌/home/yc54616/workspace/ctfh-v2-review/ARCHITECTURE_DRAFT.md:431), local candidate acceptance precedes submission. Local validation and authoritative external acceptance are different facts. An acknowledgement failure must not become either success or rejection by inference.

6. **Stop treating “deterministic runtime” as reproducible execution.**  
   Deterministic state-transition rules do not make provider responses, browser state, timing, or native agents deterministic.

7. **Replace the scheduler’s ratio with a measurable objective.**  
   “Expected score / wall-clock time” is underspecified for shared quotas, deadlines, and concurrent work. Report verified outcomes by deadline under fixed resource envelopes before claiming any scheduling policy improves performance.

**Corrected architecture**

For a controlled evaluation runner, use four boundaries:

| Boundary | Responsibility |
|---|---|
| **Evaluation supervisor** | Run identity, lifecycle, admission limits, durable metadata, deadlines, recovery status. |
| **Native-agent execution environment** | The agent’s existing tool loop, workspace, interactive processes, and native context behavior. |
| **Independent evaluator** | Ground-truth outcome checks and private evaluation material inaccessible to the worker. |
| **Experiment analysis** | Profile comparisons, uncertainty, latency attribution, and promotion decisions; outside the execution kernel. |

Store large evidence separately from transactional metadata.

The `EnvironmentProvider`/`AgentAdapter` distinction is conceptually useful **only if ownership is unambiguous**. The environment owns the execution substrate; the adapter understands the native agent’s lifecycle and observable outputs. Neither should independently own the same child process or browser context.

SQLite is a reasonable single-host metadata authority. WAL still permits only one writer at a time; “one writer per challenge” does not create parallel database writers. Keep bulky telemetry outside that write path and measure checkpoint effects before replacing SQLite. [SQLite documentation](https://sqlite.org/wal.html#concurrency)

**Corrected contracts**

These should be conformance requirements, not speculative universal APIs.

| Contract | Required correction |
|---|---|
| **Agent lifecycle** | Starting, readiness, stopping, termination, result availability, and reattachment are distinct. Unsupported observations must remain unsupported rather than becoming guessed status. |
| **Ownership** | Every mutable resource has an identified owner and sharing scope. A session name or recorded PID is not proof of current ownership. |
| **Freshness** | Evidence identifies the environment and inputs it depends on. Validity follows those dependencies, not merely a global revision counter. |
| **External effects** | State exactly which effects are mediated. Local deduplication cannot guarantee exactly-once behavior at an endpoint that lacks corresponding guarantees. |
| **Cancellation** | A stop request does not establish termination, resource release, remote cancellation, or absence of effects. Report these separately. |
| **Evidence publication** | Published evidence is complete and immutable. Missing, truncated, or interrupted capture remains visibly incomplete. |
| **Interactive output** | Cursor continuity, output gaps, session replacement, and end-of-stream must be distinguishable. Silence is not completion. |
| **Context transformation** | Derived content retains provenance and identifies omissions. Evidence retention and automatic retrieval are separate features. |
| **Evaluation** | Worker claims cannot manufacture ground truth. Evaluator credentials, answers, and private scoring material remain outside worker visibility. |

The proposed test “duplicate idempotency key does not duplicate effect” is too broad. It can establish behavior for controlled fixtures and cooperating services, not arbitrary external systems.

**Concurrency model**

The draft’s single writer, leases, and journal are **necessary bookkeeping, not sufficient isolation**.

- **Mutable ownership follows the actual resource.** Two tabs can share cookies; two processes can share files; two isolated workers can still affect one shared external instance.
- **Lease expiry is not revocation.** A stale owner may retain a live handle or an already-issued operation. Rejecting its eventual result does not undo its effect.
- **Generation checks only fence boundaries that enforce them.** A token in SQLite has no protective effect on an unmediated browser or process connection.
- **Cancellation races require separate facts.** A late verified outcome must remain recorded even if stopping was requested earlier. Cleanup status must not overwrite outcome status.
- **Resource accounting includes unresolved activity.** A locally cancelled request may still consume provider capacity. Detached children and orphaned browsers can consume slots after the parent exits.
- **Backpressure is part of correctness.** Unbounded output queues, artifact capture, and pending observations can exhaust memory or stall execution.
- **Concurrency experiments need isolation evidence.** Otherwise, increased parallelism changes the task itself and invalidates comparisons.

Process isolation is appropriate for workers and the independent evaluator. Separate processes alone do not isolate shared storage, browser profiles, credentials, or external state.

**Browser and context corrections**

The [persistent event browser](‌/home/yc54616/workspace/ctfh-v2-review/ARCHITECTURE_DRAFT.md:320) is an ownership hazard. Event-level authentication and task-level browsing have different scopes. A single persistent session must not silently become a shared mutable workspace.

Browser selection should follow required capabilities and session ownership. The draft provides no comparative latency or reliability evidence establishing a universal driver winner. Switching implementations can also change cookies, permissions, observations, and timing, making it an experimental configuration change.

A page revision does not fully capture DOM mutation or application-state freshness. Indexed observations require age and provenance; compact extraction can omit precisely the content needed to interpret a result.

The claim that raw evidence survives every transformation is also conditional. If capture occurs after RTK or native-agent compaction, the original output has already been lost. **Do not label transformed output “raw.”**

Finally, avoid two competing context managers. Native compaction plus outer summarization can duplicate latency, alter prompt caching, and discard different parts of the same evidence.

**Benchmark unit**

`HarnessProfile` is a configuration, not a complete experimental unit.

**The unit is one task instance × frozen deployed profile × evaluation protocol × repetition.**

Record:

- Exact agent version, effective configuration, model identity, reasoning settings, and observation date.
- Environment image and relevant architecture, dependencies, resource limits, and startup state.
- Permissions, tools, prompts, skills, context behavior, and any native subagent configuration.
- Task version, initial state, evaluator version, available feedback, and network conditions.
- Deadline, concurrency, quota constraints, retry policy, and cold/warm conditions.

Primary reporting should include:

- **Verified completion fraction over elapsed time**, and completion fraction at the deadline.
- End-to-end elapsed time, including preparation, queueing, and verification.
- Failure and timeout outcomes in the denominator.
- Infrastructure failures shown separately without silently excluding them.
- Cleanup latency and resource retention after termination.
- Repeated paired comparisons with uncertainty clustered by task.

Avoid these measurement errors:

- **Solved-only p50/p95:** a configuration that fails on difficult tasks can appear faster.
- **Equal tool-call budgets:** native agents expose differently sized operations.
- **Pass@k as a concurrency result:** independent repetitions and cooperating attempts are different experiments.
- **Adding stage percentiles:** overlapping spans and percentile arithmetic do not reconstruct critical-path latency.
- **Profile-only comparisons:** useful for deployment choice, insufficient for attributing improvement to one component.

Leakage controls must include shared workspaces, native memory, retrieval indexes, browser history, previous trajectories, task overlap between datasets, and repeated tuning against the same holdout. “Private” or “post-cutoff” reduces some risks; neither proves non-contamination.

**v0 scope**

For the controlled evaluation version, v0 should establish a real baseline:

1. One pinned native agent and one pinned environment.
2. Representative benign terminal and interactive fixtures, plus an independently scored evaluation corpus.
3. Minimal lifecycle supervision, evidence capture, and durable run records.
4. Independent outcome verification.
5. End-to-end latency and failure accounting.
6. Fault injection covering interrupted startup, ambiguous completion, stale observations, partial evidence, and incomplete cleanup.
7. A comparison against the same native agent without the wrapper.

That last comparison is essential: **the wrapper must account for any performance it removes.**

Move general plugins, resource transfer, multiple strategies, model routing, browser-policy composition, and elaborate retrieval outside v0. Retain only the abstractions exercised by the baseline and fault tests.

**Top 10 risks**

| Rank | Risk | Consequence |
|---:|---|---|
| 1 | Fake-only v0 | Months of architecture work without evidence about real performance. |
| 2 | Native tools bypass claimed kernel mediation | Journal and cancellation guarantees become misleading. |
| 3 | Shared task or browser state across runs | Interference corrupts outcomes and benchmark validity. |
| 4 | Leases mistaken for enforced fencing | Expired owners continue mutating resources. |
| 5 | Cancellation mistaken for resource release | Capacity leaks, quota pressure, and long tail latency. |
| 6 | Global revision used as freshness | Valid evidence is discarded while stale evidence can survive. |
| 7 | Worker completion confused with verified outcome | False success, premature termination, or lost acceptance evidence. |
| 8 | Holdout and context contamination | Apparent gains fail on genuinely unseen tasks. |
| 9 | Environment startup and tooling compatibility deferred | Missing dependencies and integration friction dominate results. |
| 10 | Routing from noisy configuration comparisons | Model and strategy decisions encode confounding rather than measured advantage. |

**Single biggest performance simplification**

**Remove the second agent framework.** Make the evaluation harness a thin lifecycle, isolation, and measurement wrapper around an intact native agent. Every additional planner, tool mediator, context layer, and routing decision should earn its place through a controlled comparison.