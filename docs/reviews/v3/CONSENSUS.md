# CTFH V3 Review Consensus

Date: 2026-10-06
Reviewers:
- GPT-6.1 Sol (xhigh)
- GPT-6 Astra (xhigh)
- Claude Opus 5.5 (max)

All three independently returned **NEEDS-CHANGES** on the first V3 proposal.

## Unanimous findings

1. The conceptual distinctions are useful, but turning Brain / Context / Hands / Tools / Skills /
   Memory into mandatory CTFH-core runtime interfaces would turn the harness into a general-purpose
   agent framework.
2. Keep the existing native-agent boundary: **AgentAdapter**.
3. Keep the existing resource-lifetime boundary: **WorkerEnvironmentProvider**.
4. Stateful terminal/browser/GUI control belongs under WorkerEnvironment first. Extract a generic
   Hand protocol only after multiple real implementations prove the common contract.
5. Do not put a ToolRouter or ToolRegistry on the native-agent hot path. Native agents keep their
   native tools, compaction, skill activation, and conversation loop.
6. Universal Hand leases/generations are unenforceable for unmediated process/socket handles.
   Shared external resources may get a dedicated broker only when the I/O boundary can actually
   reject stale operations.
7. Preserve the full v2 correctness model: single writer, TargetProvider/TargetEpoch,
   WorkerOwnerTag, cancellation barrier, narrow external-effect journal, immutable RunLock,
   OutcomeAuthority, and the real-native I14 benchmark gate.
8. WikiSkill's separation is still valuable, but live attempts should initially consume only
   **read-only, content-addressed knowledge snapshots and skill sets**. The learning-side wiki,
   including rejected hypotheses, must not automatically become agent-visible knowledge.
9. The first new CTF capability to benchmark should be **interactive sessions** (PTY/GDB/socket)
   inside WorkerEnvironment, because EnIGMA provides direct CTF evidence for this class of
   interface improvement.
10. Establish the unchanged real-native baseline before extracting or replacing any native tool
    surface.

## Corrected interpretation of the user's module vocabulary

- **Brain** = conceptual name for the complete native agent behind AgentAdapter.
- **Hands** = environment-owned capability services such as interactive sessions and browser.
- **Tools** = the native agent's tool catalog plus environment service exposure; not a kernel path.
- **Context** = native-agent context management; CTFH only materializes initial pinned inputs for
  native agents. A full ContextCompiler belongs only inside a future custom-API adapter.
- **Memory** = versioned, read-only knowledge snapshots at first.
- **Skills** = versioned Agent Skills packages, selected and pinned at admission.
- **Kernel** = durable control plane only.
- **Learning** = offline process over sealed evidence.

## Decisions adopted

- Delete mandatory Agent Runtime box.
- Delete mandatory Tool Router.
- Do not add BrainAdapter; retain AgentAdapter.
- Do not add a core Hand protocol in the first implementation.
- Do not add universal Hand leases/generations.
- Do not add live MemoryProvider write/revise on the solve path.
- Keep WorkerEnvironment as the kill domain for agent + terminal/browser/GUI child resources.
- Add AgentAdapter.materialize(...) as a pure, admission-time/native-config construction step.
- Add one environment session service API for interactive PTY-style use.
- Add one lazy managed browser service later, still inside the environment.
- Split control events from bulk trace records.
- Pin skills/knowledge/native-memory policy/capabilities in RunLock.
- Add I15-I20 invariants for the new work.
- Preserve I1-I14 unchanged.

## Final implementation validation

After implementation, GPT-6.1 Sol and GPT-6 Astra were run again against the lifecycle, artifact, trace, and learning-provenance patches. Both final blocker rechecks returned **READY** with **no blockers**.

The final regression suite passed with **102 tests**, and a real supervised Codex smoke completed with an authoritative PASS while publishing both the trace manifest and sealed attempt artifact to content-addressed storage. GPT-6.1 Sol then performed one final focused response-flush cancellation recheck and returned **READY / blockers none**.

Claude Opus 5.5 could not be rerun for the final patch-level check because the local Claude CLI OAuth session had expired and could not be refreshed. Its earlier architecture review was completed and all architecture-level findings were incorporated before implementation.

## Review files

- docs/reviews/v3/GPT6_1_SOL.md
- docs/reviews/v3/GPT6_ASTRA.md
- docs/reviews/v3/OPUS_5_5.md
- docs/reviews/v3/FINAL_GPT6_1_SOL_BLOCKER_RECHECK.md
- docs/reviews/v3/FINAL_GPT6_ASTRA_BLOCKER_RECHECK.md
