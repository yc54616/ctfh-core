## BLOCKERS

- **Target ownership conflicts with attempt ownership.** Targets may survive sequential attempts and worker restarts, but every created resource receives an attempt-scoped `OwnerTag`. Specify which owner governs persistent targets and exclude them from attempt cleanup; otherwise orphan reclamation can destroy a still-valid target. See [target lifetime](/home/yc54616/Desktop/ctfh-core/docs/ARCHITECTURE.md:222) and [resource ownership](/home/yc54616/Desktop/ctfh-core/docs/ARCHITECTURE.md:396).

- **Cancellation during STARTING lacks a completion barrier.** An in-flight environment creation could finish after cancellation’s death check. Define when provisioning can no longer create resources before declaring `CANCELLED` and releasing capacity. The existing stop-and-confirm sequence is sound once the environment exists. See [creation contract](/home/yc54616/Desktop/ctfh-core/docs/ARCHITECTURE.md:246) and [cancellation](/home/yc54616/Desktop/ctfh-core/docs/ARCHITECTURE.md:375).

- **Native-agent rate-limit guarantees are underspecified.** The scheduler requires request/token buckets, while the adapter exposes whole-attempt execution and optionally observable usage. That contract does not establish request-level enforcement. State which limits v0 actually enforces and reject unsupported hard-limit configurations; retrospective accounting is insufficient. See [adapter requirements](/home/yc54616/Desktop/ctfh-core/docs/ARCHITECTURE.md:280) and [scheduler resources](/home/yc54616/Desktop/ctfh-core/docs/ARCHITECTURE.md:438).

## NON-BLOCKING

- **v0 is thin enough.** One native adapter, local providers, direct strategy, and authoritative local evaluation form a reasonable vertical slice. Measure wrapper overhead before adding deferred modules.
- **Benchmark methodology is substantially sound.** During paired trials, match effective configuration, initial environment/target state, and timing boundaries; report deadline completion, failures, and uncertainty alongside overhead.
- **Measure integration limits:** native trace/usage coverage, cancellation latency, cleanup leaks, and output backpressure. Restricted trace visibility must constrain claims about contamination auditing.
- **RunLock timing is reconcilable:** immutable configuration capture belongs in v0; the reusable lockfile and benchmark runner can remain v1 packaging.

## VERDICT

NOT READY