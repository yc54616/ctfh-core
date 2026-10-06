## VERDICT

**NEEDS-CHANGES.**

The research gets the separations right: raw trace ≠ wiki ≠ skill, run state ≠ memory, MCP only at the edges, and OutcomeAuthority independent of the worker. Where things go is wrong. §2–3 put the agent runtime itself (Brain + ContextCompiler + Tool Router + Skills + Memory + Hands) inside CTFH. For native agents, which v2 §29 names as the main hypothesis, that runtime already exists inside Codex and Claude Code. Rebuilding it in CTFH turns CTFH into the agent framework that v2 §1 and §27 rule out.

Blocking problems:
- **New kernel leases, no new evidence.** §5 items 5 and 7, plus §6.3, add a generic Hand lease/generation subsystem. v2 §14 rejected exactly this for a reason that still holds: a database generation number cannot revoke a socket or process handle that nothing mediates.
- **Internal contradictions:**
  - **Fast path vs native-first.** The §15 fast path goes through CTFH's "TerminalHand + minimal file tools". §13.1 says native-first and §15 says "no MCP hop". A CLI agent can only get CTFH typed tools through its own extension mechanism (in practice MCP) or as shell commands. The hop-free typed-ToolRegistry path the document implies doesn't exist.
  - **Tool Router.** It is mandatory in the §3 diagram but "experimental until benchmarked" in §13.2.
  - **Kernel size.** §5 promises a kernel "smaller than the module ecosystem". It then adds three subsystems (hand sessions, generalized capability admission, leases) and leaves out the external-effect journal.
  - **Who owns terminal processes.** V3.0-B and §20 "extract" execution *out of* WorkerEnvironment. v2 §8 makes the environment the owner of PTY/debugger processes.
- **v2 invariants not carried over.** V3 never maps I1–I14. Its kernel list and diagram leave out:
  - the external-effect journal (I7/I8);
  - TargetProvider/TargetOwnerTag (I4);
  - KernelEpoch/LOST;
  - the single writer (I1);
  - pure Strategy (I10).
- **No baseline yet.** The bundle shows only a 2.87 s unit-test run, and its source-tree section is empty. There's no evidence the I14 gate (real native agent, matched A/B) exists, yet every V3 module is defined as a treatment measured against it.
- **Mostly a re-sequenced v2 roadmap.** V3.0-B, D and E reorder items v2 already planned in §24–25 (PTY/debugger support, Agent Skills packs, GhostChrome), now under new abstractions.

What V3 actually adds:
- an EnIGMA-style interactive-session API for v2's already-planned PTY support;
- capability, skill and knowledge pins in RunLock;
- an offline learning package.

Ship it as an **amendment to v2**, not a re-architecture.

## CRITICAL CHANGES BEFORE IMPLEMENTATION

1. **Change what CTFH composes.** CTFH admits an **Attempt**: a pinned AgentAdapter, a WorkerEnvironment with its admitted capabilities, and pinned content (brief, skill-set digest, knowledge-snapshot digest), run against a Target. It does not assemble an "Agent Runtime".
   - Brain, Context and Tools are internals of the AgentAdapter.
   - They only become real CTFH code inside a future custom-API adapter (v2 §9: ModelBackend is not a kernel contract).
2. **Every Hand lives inside the WorkerEnvironment's kill domain (cgroup or PID namespace). None run in the kernel process.**
   - The async Hand protocol (§7.1) and `registry.register_hand` (§17) read as if the kernel process owns them. Then a kernel crash leaves browsers and PTYs orphaned outside the environment sweep, and Hand I/O competes with the KernelLoop (I9).
   - With the environment as owner, revocation is killing the environment, and recovery is the existing LOST + WorkerOwnerTag sweep.
   - Confirm teardown by checking the kill domain is empty, not by walking parent PIDs. Browsers can spawn and reparent helper processes.
3. **Delete kernel leases and Hand generations** (§5.5, §5.7, §6.3, `sweep(live_leases)`). Keep "admitted" and "acquired" as separate ideas:
   - **Admitted.** Computed once, inside the existing all-or-nothing admission, as `requested ∩ policy ∩ available in the env template`, and recorded in RunLock.
   - **Admission only ever narrows.** Skill requirements and memory settings can drop an item or reject the profile, never add a capability. §6.2 currently lists skill requirements as an admission *input*, which brings back the "skill grants itself capability" anti-pattern.
   - **User authorization** is a static grant recorded in RunLock, never a prompt in the middle of a run.
   - **Acquired.** Lazy and owned by the environment.
   - **Brokered leases**, enforced at the I/O layer, are only for future shared resources that live outside the environment.
4. **Native-first tool policy.**
   - For native adapters, CTFH ships no `terminal.exec` and no file tools. Duplicates crowd out the tool behaviour the agent was trained on, and add schema tokens and extra turns.
   - Add only what the pinned agent lacks, starting with long-lived interactive sessions.
   - Expose them through the agent's own integration point: a CLI on PATH plus a skill, or an MCP server over stdio inside the environment. The kernel is never on that path.
5. **ContextCompiler becomes `AgentAdapter.materialize()` for native adapters.**
   - It is a pure step, hashed into RunLock.
   - It renders the brief, writes the admitted skills into the agent's native skill location, and writes the extension config into a per-attempt native config home.
   - No per-turn projection and no outer summarization. An LLM summarizer would be a hidden model call on the hot path, stacked on top of the agent's own compaction (v2 §19).
6. **Split the §14 events into two channels:**
   - **Control events** (AttemptStarted, CandidateSubmitted, Finished, Cancelled) go to the KernelLoop inbox.
   - **Trace records** (tool calls, observations) go to an append-only per-attempt file with a per-attempt sequence number, never through the kernel (I9; v2 §21 "no generic event bus").
   - Target epoch is an attribute of the attempt, not a per-event field. Adapters can't attribute it per event anyway.
7. **Write the invariant map.** Map I1–I14 to the module that enforces each, and explicitly keep:
   - the narrow external-effect journal;
   - TargetProvider/TargetOwnerTag;
   - KernelEpoch/LOST;
   - the single writer;
   - pure Strategy;
   - OutcomeAuthority as the only verification abstraction. Delete `protocols/verifier.py` and `ctfh.verifiers`; v2 §11 removed the generic Verifier and Submitter on purpose.
8. **Put contamination controls in place before any wiki or skill exists:**
   - the task sets that source traces and the task sets that evaluate promotions must not overlap;
   - learning inputs exclude evaluator secrets and OutcomeAuthority internals;
   - run a deterministic secret/flag-pattern scrub and validate `allowed-tools` before anything becomes visible to an agent;
   - every agent-visible snapshot is content-addressed and pinned.
9. **Precondition:** the v2 I14 gate (real pinned Codex, matched A/B of direct vs supervised) passes before any V3 code.

## KEEP

- **OutcomeAuthority** as the only path to SOLVED. Keep v2's authoritative/advisory and local/external declarations, per-task candidate serialization, and the journal for external effects CTFH mediates.
- **Memory ≠ run state.** Keep `memory=off|frozen|live` as a RunLock dimension (§10.3), with `off` as the default until A/B evidence says otherwise.
- **Trace, wiki and skill rules.** Raw traces stay immutable, wiki and skills are derived artifacts, and "the skill rolls back, the wiki persists".
- **Skill format.** Agent Skills with the optional `ctfh/` sidecar, and metadata-only discovery at startup.
- **MCP only at interoperability edges** (§8.4). `external:mcp:<server>` is a real capability: allowlisted, version-pinned, and its schema tokens counted.
- **Interactive sessions** as the most valuable new capability (EnIGMA), with bounded observations plus references to raw artifacts (SWE-agent ACI).
- **Browser rules.** One browser owner per attempt, no browser unless needed, observation mode as a profile field. GUI grounding is never a kernel concern.
- **Learning rules.** Learning stays offline, there's no skill mutation during a live competition, and promotion needs evidence.
- **Dependency direction** (§19), enforced by an import test. Explicit construction rather than a plugin loader (§17).
- **The §15 "no" list**, and the §13.1 adapter capability declarations, which are exactly what `materialize()` needs.
- **Side-effect classes**, in reduced form (see below).

## MERGE OR DELETE

| V3 element | Action |
|---|---|
| Brain / BrainAdapter (§4.1, §13) | Merge into the v2 AgentAdapter. `run(request, runtime)` loses Env, Sink, CancelToken, `effective_configuration()` and the usage/budget/rate/trace-coverage declarations. Keep "Brain" only as a word in the docs. |
| ContextCompiler as a core module | Merge into `materialize()`. A full compiler exists only inside a custom-API adapter. |
| Tool Router (§3) | Delete. It contradicts §13.2. |
| ToolRegistry / ToolProvider in `protocols/` | Remove from core. Tool catalogs belong to the adapter and implementations to the environment; core keeps only capability names. |
| Hand protocol in core; DebuggerHand, RemoteSessionHand | Merge into one session service inside the environment. A debugger REPL and a remote socket are both PTY sessions. |
| FileSystemHand | Delete. §7.5 already concedes it isn't needed. |
| `terminal.exec` and file tools for native adapters (§7.2, V3.0-C) | Delete. |
| Skill scripts registered as tools (§8.1) | Delete. Scripts are files that run inside the environment. |
| Script trust taxonomy (§9.3), `forbids` (§9.2), "user chooses skill" mid-run (§9.1) | Delete. The environment sandbox is the only enforcement point, `forbids` has no defined meaning, and skills are chosen at admission. |
| Capability classes `brain:*`, `skill:*`, `memory:*` | Move to RunLock fields. They select content; they aren't permissions. |
| Five side-effect classes | Collapse to `env_local`, `target`, `external`. CTFH-mediated `external` effects are journaled; the agent's own egress is governed by the environment's network policy. Privileged host actions are never admitted. |
| EpisodicMemory; TraceStore as a new store | Merge into sealed attempt directories, the content-addressed store and outcome records. Add a schema and a `trace_coverage` label. |
| `MemoryProvider.write/revise` on the live path | Delete. |
| `protocols/verifier.py`, `ctfh.verifiers`, "Critic/Verifier assistant" | Merge verification into OutcomeAuthority. Rename the Brain-side helper "advisory critic". |
| 12-field ModuleManifest | Shrink to RunLock entries `{id, kind, version, digest}` plus the per-kind declarations the kernel actually branches on. Generic `provides/requires` resolution is the seed of a plugin framework. |
| `register_hand`, `register_memory` | Delete. The factory map holds only boundaries the kernel talks to. |
| §20 package moves, and WorkerEnvironment moving into `kernel/runtime` | Defer the moves; they are churn. Never put provider implementations in core. |

## DEFER

Each item comes back only when its trigger is met.

- **DesktopHand and Computer-Use / Agent-S / UI-TARS backends.** Until a held-out task class needs GUI interaction that shell or browser can't provide. These bring their own models, which breaks the adapter's budget and rate accounting.
- **Browser with the user's real profile.** Until it is explicitly required. Even then:
  - deny by default and require a per-run human grant recorded in RunLock;
  - run it through a dedicated broker with a lease enforced at the I/O layer;
  - never let attempt cancellation close it.
  This is the only lease the design actually justifies.
- **browser-use.** It is an agent framework. At most its session layer could qualify, and only after an A/B.
- **Extra browser observation modes.** Ship one default: a snapshot of interactive elements with refs, plus a screenshot on demand.
- **MemoryProvider and vector/graph/A-MEM/Letta backends, and live memory writes.** Until frozen file snapshots win an A/B.
- **A full ContextCompiler.** Until a custom-API adapter exists.
- **Planner/router/critic policies and the OpenHands / ComputerUse adapters.** v2 §26 conditions stand. OpenHands also brings a second sandbox owner.
- **Mid-run capability requests (§16) and context injection.** On a TargetEpoch change, cancel and restart. Native resume can come later as a declared adapter capability.
- **Typed debugger tools.** Until they beat the raw session service in an A/B.
- **SkillWeaver-style learned tool APIs.**
- **V3.0-G skill evolution.** Until hand-written skills win an A/B and a separate held-out corpus exists.
- **Entry-point discovery.** Until two independent third-party implementations exist (v2 §21).
- **V3.0-A "interfaces only".** Extract each protocol when its second real implementation lands.

## CORRECTED MODULE BOUNDARIES

```text
ctfh_core (kernel process; imports no implementations)
  KernelLoop: task/attempt/external-op state machines, single-writer SQLite,
    scheduler + pure Strategy, supervisor, all-or-nothing admission incl. static
    capability set, RunLock, ArtifactStore/CAS, narrow journal, reaper, span timestamps
  Protocols: TaskSource, TargetProvider, WorkerEnvironmentProvider, AgentAdapter,
    OutcomeAuthority                                   -- nothing else
  Data only: capability names, side-effect classes, RunLock / control-event /
    trace-record schemas
  Governance: admission-time only -> network policy + admitted caps + brief

WorkerEnvironment impl (one kill domain; swept by WorkerOwnerTag)
  workspace, network policy (the real scope enforcement), installed tools,
  per-attempt native config home,
  services: session (PTY / debugger REPL / remote socket), browser (lazy), [desktop later]
  read-only mounts: skill set, knowledge snapshot

AgentAdapter impl (per native agent)
  materialize(brief, skills, knowledge, caps) -> native config; launch/cancel;
  parse native stream -> trace file + control events; capability declarations
  [custom-API adapter only: own loop, context manager, tool catalog]

ctfh_learning (offline process; reads sealed artifacts; writes new snapshot versions)
```

| Resource | Owner | How it's revoked | How it's recovered |
|---|---|---|---|
| Task/attempt state | KernelLoop | Compare-and-swap state transition | Marked LOST on a new KernelEpoch |
| Environment, agent and services | WorkerEnvironmentProvider | Kill the kill domain | Sweep by WorkerOwnerTag |
| Target instance | TargetProvider | Reset, which increments TargetEpoch | Reconcile against the target registry |
| Submissions and instance start/reset | Kernel journal | — | Idempotency key or reconcile probe; UNKNOWN is never blindly retried |
| Traces and artifacts | ArtifactStore | Immutable after sealing | Unsealed output is not authoritative |
| Skill sets and knowledge snapshots | Written by the learning plane, pinned by RunLock | Re-pin | Rebuildable from sealed traces |
| Shared resources outside the environment (deferred) | A dedicated broker | Lease enforced at the I/O layer | Broker-specific |

Answers to the bundle's questions:
- **Q1.** For native agents, Brain and Context are artificial boundaries. Hands and Tools are environment capabilities. Memory and Skills are content.
- **Q3.** No kernel leases now.
- **Q5.** Hybrid, with hard lines: core owns the names, the environment owns implementations, the adapter owns catalogs.
- **Q7.** The minimum surface is the five v2 protocols, the new RunLock fields, `materialize()`, and one session-service API. That API belongs to the environment package, not to core.

## HANDS / BROWSER / TERMINAL REVIEW

- **The concept is right, the placement isn't.** A stateful session with a lifecycle, bounded observations and raw artifacts is correct. A kernel-registered Hand with owner, generation, reconcile and sweep duplicates the environment's `health_check`, `kill` and `sweep_workers`, and gives in-environment processes a second lifecycle owner.
  - Reverse V3.0-B: terminal execution stays *in* the environment.
  - "Durable session IDs" (research §2) should mean durable across agent turns, not across crashes. Write that down so nobody builds session persistence.
- **Minimum session service.**
  - Operations: `open(argv, cwd)`, `send(id, data)`, `read(id, since_offset, max_bytes, wait_ms | until_quiescent)`, `signal`, `close`, `list`.
  - Reads that take an offset and can wait stop the agent from spending turns busy-polling.
  - A PTY has no separate stderr, so the §7.2 observation schema has to vary by mode.
  - Raw transcripts go to the attempt directory, with per-attempt caps on session count, bytes and disk. Extend the v2 output-flood fault test to cover this.
  - Session IDs are scoped to the attempt; no generation number is needed.
- **Exposure.** A/B two options: a CLI on PATH plus a skill (no schema tokens, uses the native shell) against an MCP server over stdio inside the environment. At attempt start, check the agent actually loaded it and fail closed if not (v2 §9).
- **Browser.**
  - Admit it up front and start it lazily inside the environment. This replaces §16's mid-run round-trip to the kernel.
  - Reserve browser memory in the environment slot class at admission, so a lazy start can't run out of memory.
  - Keep the profile directory inside the disposable workspace and send downloads to the attempt's artifacts.
  - §16's "reuses or creates" must mean reuse within one attempt only. Reuse across attempts breaks v2 §22 isolation.
  - "The Brain never starts a second unmanaged browser" has nothing to enforce it against a native shell. Relabel it as guidance plus a process audit, with the kill domain as the backstop.
- **Fencing.** On a target reset, cancel the attempts bound to the old epoch before admitting new actions. Tagging sends with an epoch would only fence part of the traffic, because the agent's own shell goes around it. The resource siblings actually share is the *target*, which TargetProvider already models; §6.3 fences the wrong layer.
- **GUI.** Deferred. If it's ever needed: a virtual display inside the environment plus a control service, screenshots to artifacts, and grounding kept on the adapter side.

## MEMORY / SKILLS / WIKISKILL REVIEW

- **Three artifact classes, each with its own lifecycle:**
  - **Sealed traces.** These already exist. Add a schema and a `trace_coverage` label, because native agents may hide raw tool output (v2 §9–§10).
  - **Knowledge snapshots.** Versioned files.
  - **Skill sets.** Versioned Agent Skills directories.

  EpisodicMemory, GraphMemory and MemoryProvider add stores without any evidence they help.
- **Split V3's Knowledge Wiki in two:**
  - **The learning-side wiki.** State for the maintainer and proposer, including rejected changes, `skill-impact.md` and evaluation statistics. Agents never see it.
  - **The agent-visible knowledge snapshot.** A curated export. It is itself a treatment and gets pinned.

  Without this split, "the wiki persists either way" lets content from rejected candidates reach live context without ever being benchmarked.
- **The live path is read-only.**
  - `frozen` mounts the snapshot as read-only files the agent searches with its own tools. Optionally add a small digest chosen at admission from task metadata.
  - No live writes means no concurrency rules to design and no contamination between sibling attempts.
  - The native agent's own memory features are pinned off or isolated in the per-attempt config home, and that is recorded.
  - The §10.2 end-of-attempt processor runs after sealing, off the path that releases capacity (v2 §13), and preferably in offline batches.
- **Skills.**
  - v2 §20 was right: skills are profile content, not a subsystem.
  - At admission: select the set, check its requirements (drop or reject, never expand), and hash it.
  - Write skills into the agent's native skill location and let its own progressive disclosure activate them. Native adapters get no CTFH `activate_skill`.
  - Isolate the config home. Otherwise the user's own skills leak in and the RunLock no longer describes the run.
  - Strip or validate `allowed-tools`. Its meaning is up to each client and can pre-approve tools — the "skill grants itself tools" anti-pattern V3 lists.
- **The WikiSkill loop in CTFH terms.**
  - One atomic candidate change produces one new skill-set digest.
  - The gate is the existing matched-pair benchmark runner, on a task set that doesn't overlap the source traces.
  - Accept only if verified solves by deadline go up and wall-clock stays within a declared regression budget.
  - Rollback means re-pinning the previous digest.
  - The learning plane's model usage never shares provider quota with a live event.

## PERFORMANCE FAST PATH

```text
TaskSpec + IntentBrief
 -> admission: slots + static caps (template-cached probe) + RunLock      [kernel, once]
 -> warm env from template (service binaries present, not running)
 -> adapter.materialize() + launch pinned native agent                     [pure, cheap]
 -> native loop on native tools; session/browser services start on first use, in-env
 -> candidate submitted mid-run -> control event -> OutcomeAuthority
 -> PASS: CAS commit -> sibling cancel -> seal/promote; slow cleanup async
```

- **Kernel contact.** Between launch and the first candidate, the kernel only sees control events.
  - Candidate submission is the one call from the agent to the kernel. Allow it mid-run, so "first valid candidate latency" measures something real.
  - Rate-limit submissions and serialize them per task.
  - Whether the verdict is fed back to the agent is a treatment, and it is recorded.
- **Regressions to guard against:**
  - leases or admission checks on the tool path;
  - schema-token bloat;
  - duplicate tools adding turns;
  - per-turn context compilation or LLM summarization;
  - starting the browser or services eagerly;
  - memory retrieval calls;
  - trace records fanning out through the kernel inbox;
  - polling service health;
  - learning jobs competing for CPU or provider quota;
  - a package restructure delaying the baseline.
- **Likely bigger levers than any V3 module:**
  - an environment warm pool with copy-on-write templates (v2 v1);
  - service binaries pre-installed in the template;
  - capability probes cached per template.
- **Measurement.**
  - Add timing spans for service first start, latency per service call, the static count of added schema tokens, and time to first tool call.
  - Declare numeric overhead budgets against direct native, such as the change in agent start time and in p95 per-call latency, and fail the gate when one is exceeded.
  - Fix the §22 pair "direct native vs V3 core + TerminalHand", which mixes two treatments. Compare direct vs supervised first, then supervised vs supervised plus sessions.
  - Drop the "stale-action rejection" metric unless mediated fencing exists.

## MIGRATION ORDER

0. **Gate:** v2 v0 is done, meaning the I14 end-to-end run with a real agent and the direct-vs-supervised A/B both pass.
1. Amend V3 per the critical changes, add the I1–I14 map, and add I15–I20 as tests, starting with import direction.
2. Add the RunLock and admission fields: capabilities, skill-set digest, `memory=off`, service versions. Add the "admission only narrows" check and the cached template probe. No file moves.
3. Build the session service inside the environment (local first, then Kali/Docker) and wire it to Codex. A/B native-only vs native plus sessions on a subset of tasks that need interactive sessions.
4. Add hand-written skills through `materialize()`, with config-home isolation and `allowed-tools` validation. A/B them.
5. Add the Claude Code adapter. Extract the AgentAdapter protocol from the two real implementations and add conformance tests.
6. Add GhostChrome as a lazy service inside the environment. A/B it on the tasks that need a browser vs shell/HTTP only.
7. Add the trace schema and coverage labels, and create `ctfh_learning`.
8. Build an offline knowledge snapshot from a training split, with the contamination controls, and test `frozen` as a treatment.
9. Run offline skill proposal and promotion through the benchmark runner, with rollback by re-pinning.
10. Bring back deferred items only when their triggers are met.

## TOP 5 CONCRETE CHANGES

1. **Rewrite §2 and §3** to say: "The kernel admits an Attempt — a pinned AgentAdapter, a WorkerEnvironment with its admitted capabilities, and pinned content — against a Target. OutcomeAuthority alone commits SOLVED. Learning runs offline over sealed artifacts." Delete the Agent Runtime box, the Tool Router, and the six concepts as code boundaries.
2. **Delete §5.5, §5.7, §6.3 and `Hand.sweep(live_leases)`** and insert: "Admitted = requested ∩ policy ∩ available in the template, computed once and stored in RunLock. Metadata can only narrow it. Acquisition is lazy and owned by the environment. Revocation is tearing down the environment's kill domain. Brokered leases are only for future shared resources outside the environment."
3. **Replace §7 with "Environment capability services":**
   - one session service (the API above, raw transcripts, caps) and one lazy browser service, both inside the environment's kill domain;
   - both exposed through the native agent's own integration point;
   - no CTFH `terminal.exec` or file tools for native adapters;
   - desktop and the real-profile browser deferred.
4. **Replace §12 and §9.1's runtime activation with `AgentAdapter.materialize(brief, skill_set, knowledge_snapshot, admitted_caps) -> native config`.**
   - It is pure, hashed into RunLock, and runs in a per-attempt config home.
   - It validates `allowed-tools` and mounts knowledge read-only.
   - Also split §14 into kernel control events and trace records that only go to a file.
5. **Replace §21 with the gated order above and add executable invariants:**
   - **I15:** no agent tool or observation path reaches the KernelLoop or SQLite, except candidate submission.
   - **I16:** every service process dies when its environment's kill domain is killed.
   - **I17:** admission only ever narrows the requested capability set.
   - **I18:** every skill set and snapshot an attempt sees is content-addressed in its RunLock, and attempts can't write to them.
   - **I19:** learning inputs exclude evaluator secrets, and source and evaluation task sets don't overlap.
   - **I20:** `ctfh_core` imports nothing from builtin, adapters or learning.

---
*Separate from this review: the claude.ai Gmail, Google Calendar and Google Drive connectors need to be authorized in your claude.ai connector settings and won't work until then.*
