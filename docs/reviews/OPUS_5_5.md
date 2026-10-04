# Architecture review: modular interactive runtime

The overall direction is right. The task is the durable unit, workers are disposable, one party writes state, and only side effects are journaled.

The problem is the machinery. It describes a distributed system, with per-task coordinators, generation fencing on every lease, general reconciliation, a value-optimizing scheduler, entry-point plugins and about 12 module types. But it runs on one host, and most of its wall-clock time goes to model calls and environment startup. The v0 should be much smaller.

---

## 1. KEEP

- **The task is the durable unit.** Attempts are disposable and conversations are never resumed. Recovery means killing the attempt, starting a new one and letting the strategy decide what to do next.
- **One authoritative writer.** Keep the rule, but implement it as a single kernel loop (see §9), not N concurrent coordinators.
- **Storage.** SQLite in WAL mode holds control state. Artifacts are immutable, content-addressed files on disk.
- **No DB transaction spans an external wait.** Make this mechanical: the store API is synchronous, transactions are short, and a transaction is never held across an `await`.
- **A journal for non-idempotent effects**, with the narrower scope described in §3.
- **Stale-result rejection**, keyed on attempt identity and epoch (see §3).
- **The HarnessProfile is what gets benchmarked**, with full version hashes.
- **Strategy defaults.** One strong direct worker by default. pass@k for hard tasks. Planner/executor only when there's evidence it helps. Prefer coherent same-model setups.
- **Browser rules.** The BrowserDriver owns its session. Authenticated state is never migrated silently. There is one lifecycle owner.
- **Context rules.** Capture raw output first. Optimizers only produce views. Compaction is never the authorization layer.
- **Plugins.** Typed Protocols, no event bus on the hot path, and a conformance test suite for each contract.
- **The scheduler admits work; it never changes a running worker's intent.**

## 2. DELETE / DEFER

| Item | Action | Reason |
|---|---|---|
| ModelBackend as a kernel contract | **Demote** | Make it private to the custom-API AgentAdapter. The kernel never talks to models. |
| ToolAdapter, ContextView, SkillPack | **Defer** | These are AgentAdapter internals. The kernel only needs their content hashes in the profile lockfile. |
| BrowserDriver, BrowserDecisionPolicy | **Defer** | When added, the browser becomes a capability of `Env`, not a separate lifecycle. |
| Package entry-point loading | **Defer** | v0 uses an explicit `dict[str, Factory]` registry. Add entry points when third-party plugins exist. |
| Per-task coordinator as a concurrent entity | **Delete** | Keep it as a logical state machine inside one kernel loop. |
| Generation fencing on every lease | **Delete (v0)** | Replace with a process-level kernel epoch, private resources per attempt, and killing the old holder before reassigning. |
| Expected-value/wall-clock optimization, uncertainty modeling, preempt, park | **Defer** | v0 uses slot caps, priority, deadline-aware admission and rate buckets. |
| Easy/hard task classifier | **Defer** | v0 sets k per task family or per profile. |
| Planner/executor, heterogeneous mixing, parallel hypotheses | **Defer** | Each needs benchmark evidence first. |
| Online pass@k and partial-progress metrics | **Defer** | v0 records raw per-attempt facts; aggregate them offline. |
| UNKNOWN as an attempt or task state | **Delete** | UNKNOWN only applies to journaled operations (§6). |

## 3. CHANGE

1. **Narrow the journal.** Journal an effect only if it (a) leaves the attempt's disposable environment and (b) is non-idempotent or limited by quota. Each journaled operation type must declare one of three things when it's registered: a remote idempotency key, a `reconcile()` probe, or an explicit statement that UNKNOWN is an acceptable final state. Refuse to register types that declare none of these. Effects inside the environment are never journaled, because the environment itself is the transaction boundary.
2. **Workers get no DB handle.** Lifecycle events go to the kernel inbox. Bulk output (transcripts, tool stdout) goes straight to append-only files private to the attempt. Nothing is fsynced per token or per tool call.
3. **Replace "base revision" with identity + state compare-and-swap.** Use `(task_id, attempt_id, epoch)` plus `UPDATE … WHERE state=? AND epoch=?`. pass@k attempts share no mutable state, so there's nothing to merge. The only contested write is the task's final outcome, and only the kernel makes it.
4. **Separate attempt termination from resource cleanup.** An attempt is finished once its process tree is confirmed dead, not once everything is torn down. Teardown failures go on a leak list for the reaper. Task completion never waits on cleanup.
5. **EnvironmentProvider must provide a reliable kill and an orphan sweep by owner label.** Without these, cancellation is not safe, however well designed the state machine is.
6. **Strategy is a pure synchronous function: `decide(view) -> commands`.** No I/O, no awaits, no clock (the current time is passed in the view). That makes it replayable and fuzzable, and takes it out of the concurrency problem.
7. **The scheduler is part of the kernel in v0, not a plugin.** Slots are acquired all-or-nothing inside the kernel loop, so a task can never hold some slots while waiting for others. That rules out deadlock.
8. **Model rate limits are a scheduled resource.** Keep request and token buckets per provider key, and admit pass@k fan-out against them. Otherwise pass@k causes bursts of 429 errors and drives p95 up.
9. **Resolve the HarnessProfile to a lockfile when a task is admitted.** It records digests for the adapter version, prompts and skills, environment template, tool versions, model ID and parameters, and strategy parameters. The hash is fixed for the task's lifetime.
10. **Verifiers declare their authority: `authoritative` or `advisory`.** Only an authoritative pass can commit the task outcome or cancel sibling attempts.
11. **Latency breakdown is in v0 scope.** Record per attempt: queue wait, environment ready, first model call, total model time, total tool time, verify time, teardown. You can't tune wall-clock time without knowing the critical path.
12. **Restart recovery.** Bump the epoch, mark live attempts LOST, sweep orphans, reconcile journaled operations still marked RUNNING, then let the Strategy decide again within the remaining deadline.

## 4. Corrected kernel boundary

```
               ┌──────────────────── kernel process (1 per host, flock + epoch) ───────────────────┐
 TaskSource ──►│ bounded inbox ─► KernelLoop (single-threaded; owns all task/attempt/resource state)│
               │                    │  Strategy.decide()  (pure, in-loop)                           │
               │                    │  Scheduler: slots, rate buckets, deadline admission           │
               │                    ▼                                                               │
               │   group-commit ──► Store (SQLite WAL, 1 writer thread)    ArtifactStore (CAS)      │
               │                    │ (commands are dispatched only after their batch commits)      │
               │                    ▼                                                               │
               │              Supervisor: spawn / deadline / cancel / kill / reap                   │
               └───────┬───────────────────────┬────────────────────────┬──────────────────────────┘
                       ▼                       ▼                        ▼
             EnvironmentProvider          AgentAdapter            OutcomeVerifier
             (one Env per attempt)   (subprocess or async task)   (pure or journaled effect)
                       └──── bulk output ──► attempt-private append-only dir (sealed and hashed at end)
```

**What the kernel owns:** domain types and state machines, the kernel loop, the store, the artifact store, the journal protocol, the resource registry (slots, buckets, leases, reaper), the scheduler, deadlines and cancel scopes, the factory registry, and profile resolution.

**Plugins** do I/O. They may hang, crash or misbehave, and the kernel supervises them under deadlines. Strategy is the one plugin that runs inside the loop, so it must be pure and bounded in time.

**Kernel invariants.** Each of these should be a test:
- **I1.** Only the kernel writes to the store.
- **I2.** No command is dispatched before the state transition that caused it is committed. This gives write-ahead logging for free, through group commit.
- **I3.** Every external resource carries an `OwnerTag` from the moment it's created. Every tagged resource is either leased to a live owner or will be swept.
- **I4.** An attempt is terminal only after its process tree is confirmed dead.
- **I5.** A journaled effect runs only after its operation row is durably RUNNING. Every operation ends SUCCEEDED, FAILED or UNKNOWN.
- **I6.** The task outcome is committed only from an authoritative verdict, through a single compare-and-swap from a non-terminal state.
- **I7.** No `await` while a transaction is open.
- **I8.** `Strategy.decide` is pure.

## 5. Corrected module contracts

```python
@dataclass(frozen=True)
class OwnerTag:  run_id: str; epoch: int; task_id: str; attempt_id: str

class CancelToken(Protocol):
    deadline: float                      # monotonic, = min(parent, own)
    def cancelled(self) -> bool: ...
    async def wait(self) -> None: ...

class TaskSource(Protocol):
    def load(self, selector: str) -> Iterable[TaskSpec]   # frozen, content-hashed; inputs are artifact refs

class EnvironmentProvider(Protocol):
    name: str; version: str; capabilities: frozenset[str]
    async def create(self, spec: EnvSpec, owner: OwnerTag, cancel: CancelToken) -> "Env"   # tags resources with owner
    async def destroy(self, owner: OwnerTag) -> None          # idempotent; works from a tag after a restart
    async def sweep(self, run_id: str, live: Collection[OwnerTag]) -> list[Leak]   # destroy untracked tagged resources

class Env(Protocol):
    owner: OwnerTag
    async def exec(self, argv, *, cwd, env, timeout, stdout: Sink, stderr: Sink) -> ExecResult: ...
    async def read(self, path) -> bytes: ...
    async def write(self, path, data: bytes) -> None: ...
    async def kill(self) -> None          # hard, fast, confirmed (cgroup.kill / killpg + pidfd)

class AgentAdapter(Protocol):
    name: str; version: str; capabilities: frozenset[str]
    async def run(self, req: AttemptRequest, env: Env, out: AttemptSink, cancel: CancelToken) -> AttemptResult
    # Must stop cooperatively within req.grace_s; the kernel kills the Env regardless.
    # Writes only to env and out; reports usage incrementally through out.usage() (coalesced).

class Strategy(Protocol):
    def decide(self, view: TaskView) -> Sequence[Command]
    # Command = StartAttempt(variant) | CancelAttempt(id, reason) | Verify(attempt_id)
    #         | Commit(attempt_id) | Finish(outcome)
    # TaskView is an immutable snapshot: spec, profile params, attempts, verdicts, usage, now, deadline, budget

class OutcomeVerifier(Protocol):
    authority: Literal["authoritative", "advisory"]
    effect: Literal["none", "external"]
    async def verify(self, task: TaskSpec, candidate: CandidateRef, cancel: CancelToken) -> Verdict
    async def reconcile(self, op: OpRecord) -> Verdict | Unknown   # required iff effect == "external"
```

- AttemptRequest, AttemptResult and Verdict are serializable dataclasses. A Verdict holds pass/fail/error, an optional score and an evidence artifact reference.
- The kernel creates and owns each `Env`, so its lifetime is never tied to the adapter's.

## 6. Concurrency, cancellation and resource model

### Concurrency

- **One kernel process per host.** It takes an exclusive `flock` and increments a stored epoch at startup. A second instance fails fast.
- **The kernel loop is one asyncio task** reading a bounded inbox. Handlers are synchronous and keep state in memory. Each batch of events (up to about 5–10 ms or N events) runs in one transaction: apply the events, commit, then dispatch the resulting commands.
  - Use `synchronous=FULL`. Group commit spreads the fsync cost, and a few milliseconds is negligible next to model latency measured in seconds.
- **SQLite:** one writer thread owns the connection. Dashboards and exports use separate read-only WAL connections.
- **Workers:**
  - Native harnesses run as subprocesses; API adapters run as supervised tasks.
  - Tool execution always happens in subprocesses inside the `Env`.
  - Hashing and parsing run in a thread or process pool.
  - Track event-loop lag as a metric.
- **Bulk data** never goes through the inbox. At attempt end, the attempt directory is sealed (hashed, fsynced, renamed) and indexed.

### Cancellation

Scopes nest: Run ⊃ Task ⊃ Attempt ⊃ (Exec | Operation). Each scope has an absolute monotonic deadline.

```
Attempt:  QUEUED → STARTING → RUNNING → FINISHED{completed|gave_up|error}
                       └──── cancel ────┴─► CANCELLING ─(tree confirmed dead)─► CANCELLED
          restart: STARTING | RUNNING | CANCELLING ─► LOST

Op:       PENDING(queued) → RUNNING(durable before effect) → SUCCEEDED | FAILED
          RUNNING after a crash or timeout → RECONCILING → SUCCEEDED | FAILED | UNKNOWN (final for automation)

Task:     … → CANCEL_REQUESTED → (attempts killed, ops reconciled) → CANCELLED [+ unknown_ops list]
```

Cancelling an attempt:
1. Record CANCELLING and trip the cancel token.
2. Give the adapter a cooperative grace period (2–5 s, tunable; zero when cancelling siblings after a success).
3. Call `env.kill()` and confirm the process tree is dead.
4. Mark the attempt CANCELLED and release its compute slots.
5. Run `destroy` asynchronously. Environment capacity is released when destroy succeeds; a failed destroy goes on the leak list.

Two more rules:
- Abandon in-flight model HTTP calls by closing the connection; don't wait for them.
- Journaled effects are shielded from cancellation once they've been dispatched. If one times out, it goes to RECONCILING. UNKNOWN operations are never retried automatically unless they have an idempotency key.

### Resources

- **Resource classes:** compute slots, environment instances, model request and token buckets (per provider key), external quotas, and later browser sessions.
- **Admission** is all-or-nothing for the attempt's full resource set, computed inside the loop. Because nothing holds resources while waiting for more, deadlock can't happen.
- **Leases:** the lease row (kind, `OwnerTag`, state) is committed before the resource is created (invariant I2), and creation is tagged. At startup the reaper sweeps tagged resources that don't belong to a live owner in the current epoch.
- **Fencing:**
  - The kernel epoch is the only fencing token.
  - Attempts write only to their own private directories, so late writes from a dead attempt land somewhere that is never promoted.
  - Exclusive resources (authenticated sessions, external accounts) are reassigned only after the previous holder is confirmed dead. They are never migrated.
- **Rate buckets** are in-memory only and rebuilt conservatively after a restart.

## 7. v0 implementation order

Each step ends with its own tests.

1. **Domain types, pure state machines, `DirectStrategy`.** Property tests: random event sequences never break I1–I8.
2. **Store:** schema, migrations, writer thread, group commit, compare-and-swap updates, epoch and flock. Crash tests: `kill -9` in the middle of a batch.
3. **ArtifactStore:** private attempt directories, append-only segments, sealing, the index.
4. **Kernel loop, supervisor, cancel scopes and deadlines**, tested against a scriptable `FakeAgent` (hang, ignore cancel, crash, flood events) and a `FakeEnv`.
5. **LocalEnvironmentProvider:** clone the workspace from a template, run in a cgroup or process group, kill, sweep by tag. Gate: kill the kernel mid-run, restart, and the sweep must find zero leaks.
6. **TaskSource (local manifest), LocalVerifier (authoritative), outcome commit, metrics export** with the per-attempt latency breakdown.
7. **One real AgentAdapter plus the HarnessProfile lockfile.** Run a small benchmark slice and profile the critical path. Revise the contracts here, based on real data.
8. **Scheduler:** slot caps, rate buckets, deadline-aware priority admission.
9. **Journal**, tested with a fake external effect that injects ambiguity (a timeout after the effect has happened) to exercise RECONCILING and UNKNOWN.
10. **PassAtK strategy** with first-authoritative-success sibling cancellation. Measure wall-clock time against k.
11. **Offline replay simulator** that feeds recorded attempt durations and outcomes to Strategy and Scheduler. Any smarter policy must beat the greedy baseline here first.

After v0: entry points, the custom API adapter with ModelBackend, an environment warm pool, browser support.

## 8. Top 10 risks

1. **Durable writes on the hot path.** An fsync per event or tool call turns the kernel into the bottleneck and inflates p95. *Mitigation:* group commit, bulk output to files.
2. **Leaked processes or environments after a crash or cancel**, causing resource exhaustion and contamination between attempts. *Mitigation:* tag resources at creation, kill via cgroup, sweep at startup, and fail CI if the leak count is above zero.
3. **Journal operations that can never be resolved**, leaving tasks stuck or effects duplicated. *Mitigation:* operation types must declare idempotency or reconciliation when registered.
4. **Contracts shaped by the fakes.** *Mitigation:* a real adapter by step 7; contracts stay unstable until two implementations exist.
5. **Hidden shared bottlenecks** (rate limits, CPU, disk I/O) that make pass@k slower than k=1. *Mitigation:* rate buckets, capacity-based admission, recording CPU throttling per attempt.
6. **False positives from the verifier** cancelling sibling attempts that would have succeeded. *Mitigation:* the authority flag; advisory verdicts never cancel.
7. **Broken attempt independence** (shared caches, home directories, session state), which biases pass@k. *Mitigation:* one environment per attempt from an immutable template, and any shared cache declared and hashed in the profile.
8. **Incomplete version hashing**, making results irreproducible and leading to wrong profile decisions. *Mitigation:* refuse to run profiles that aren't pinned.
9. **asyncio pitfalls:** blocking calls on the loop, swallowed `CancelledError`, orphaned tasks. *Mitigation:* `TaskGroup`, linting against broad `except`, the loop-lag metric, and a hard kill that doesn't rely on cooperative code.
10. **Scheduler sophistication before there's data.** *Mitigation:* the greedy baseline plus the replay simulator; a smarter policy must win on recorded traces before it ships.

## 9. Biggest simplification: one kernel loop, one writer, disposable attempts, environment as the transaction boundary

- **One loop owns everything.** Replace the N per-task coordinators, the separate scheduler and per-lease fencing with a single-threaded kernel loop. It owns all state in memory, persists it through group commit, and dispatches commands only after they're committed.
- **Never resume an attempt.** A crash makes the attempt LOST; the reaper sweeps, and the strategy decides again.
- **Journal only what leaves the environment.** Only effects that escape the disposable environment are journaled.

What this removes:
- lock and compare-and-swap contention, and SQLite busy retries
- fencing tokens on every lease
- most reconciliation paths

What it gains:
- Cancellation reduces to a confirmed process-tree kill.
- Strategy and Scheduler become deterministic and replay-testable.
- Each state transition costs about one batched commit (milliseconds), next to model and tool latencies of seconds.

Further wall-clock gains, in expected order of impact:
1. **Environment warm pool and copy-on-write template cloning.** Startup often dominates short tasks.
2. **Sibling cancellation on the first authoritative success** in pass@k.
3. **Hedged pass@k.** Start one attempt. Launch extra attempts only once elapsed time passes the historical p50 time-to-success for that task family. This keeps most of pass@k's tail-latency benefit at a fraction of the cost.
4. **Rate-limit-aware admission**, to prevent bursts of 429 retries.
5. **Keep bulk I/O and hashing off the event loop.**
