# Modular Agent Architecture Landscape for CTFH

Date: 2026-10-06
Status: research synthesis for architecture redesign

## Executive conclusion

The current CTFH implementation is strong in control-plane correctness, intent preservation,
state recovery, target epochs, and outcome authority, but it does not yet expose the execution
surface as first-class replaceable modules. Browser, GUI, terminal interaction, tools, skills,
memory, and context are mostly described in documentation or inherited from the native agent
profile rather than represented as explicit runtime capabilities.

The literature and open-source systems converge on a cleaner separation:

1. **Brain** decides what to do. It should not own browser/session lifecycle, persistent memory,
   or skill storage.
2. **Hands** own stateful external interaction sessions: browser, desktop GUI, terminal/PTY,
   debugger, remote shell, etc.
3. **Tools** are typed atomic actions exposed to the brain. A tool may be stateless or may be
   bound to a Hand session.
4. **Skills** are reusable procedural packages that tell the brain how to combine tools/hands.
   Skills are not tools and are not raw memories.
5. **Memory** stores durable experience and knowledge. Raw traces, episodic summaries, semantic
   knowledge, and procedural skills should not be collapsed into one store.
6. **Context** is a projection layer. It selects what the brain sees from intent, memory, skills,
   tools, observations, and recent trajectory. It is neither memory nor the brain.
7. **Kernel/Runtime** owns scheduling, capability leases, resource lifecycle, isolation,
   persistence, cancellation, recovery, provenance, and authoritative verification.
8. **Learning/Evolution** is an outer loop over traces and evaluation. It should not sit on the
   hot path of a CTF solve by default.

This design preserves CTFH's thin-supervisor principle while making browser control, skills,
tools, and memory true replaceable modules.

## Research coverage

The architecture was derived from a broad search across agent-skill evolution, web/GUI agents,
CTF agents, memory systems, agent runtimes, tool protocols, and agent operating systems.
The search phase reviewed hundreds of candidate results and then deep-read primary papers,
official specifications, and maintained repositories.

High-signal families:

- Skill evolution: WikiSkill, Trace2Skill, EvoSkill, SkillWeaver, Voyager, OpenSkill,
  SkillMaster, SkillAdam, TRACE/skill-bank work.
- Browser and computer use: BrowserGym/AgentLab, WebArena, WebVoyager, browser-use,
  Agent-S, UI-TARS, OSWorld, OS-Copilot/FRIDAY, UFO, SWE-agent ACI.
- Cybersecurity/CTF: EnIGMA, NYU CTF Bench, CyBench/InterCode-style agent environments.
- Memory: MemGPT/Letta, A-MEM, MemoryBank, procedural-memory work such as Memp,
  graph/episodic-semantic memory systems.
- Runtime/orchestration: OpenHands, AutoGen, LangGraph, AIOS, AgentScope Runtime,
  smolagents.
- Interoperability: Agent Skills specification and Model Context Protocol (MCP).

## 1. Skill systems

### WikiSkill

Primary source:
- https://arxiv.org/abs/2608.27454

WikiSkill's most important architectural contribution is not merely "automatic skill writing".
It explicitly separates three layers:

- **Raw Layer**: immutable execution traces.
- **Wiki Layer**: persistent, structured, compounding knowledge.
- **Skill Layer**: executable procedural knowledge.

Its outer loop has four responsibilities:

- Inference Agent executes rollouts with the current skill set.
- Wiki Maintainer consolidates successes/failures into patterns and evolution history.
- Skill Proposer reads the wiki and selected traces to create one atomic skill change.
- Validation gating accepts or rolls back the candidate skill.

A critical detail for CTFH: **the skill can roll back while the wiki persists**. Rejected changes
are still useful evidence. This means CTFH should never treat "active skill files" as the only
persistent learning state.

### Trace2Skill

Primary source:
- https://arxiv.org/abs/2603.25158

Trace2Skill separates:

1. trajectory generation,
2. parallel success/error analysis,
3. conflict-free many-to-one consolidation.

It finds that broad trajectory evidence can be distilled into a portable skill directory and that
consolidated skills can outperform retrieval of isolated trajectory lessons. General procedures
belong in SKILL.md; narrow cases can live in on-demand references.

Lesson for CTFH:
- trajectory memory and procedural skills need separate stores;
- skill generation should consume multiple traces, not blindly patch after each single failure;
- deterministic format/conflict validation is valuable before promotion.

### EvoSkill

Primary source:
- https://arxiv.org/abs/2603.02766

EvoSkill operates at the skill-folder level instead of optimizing prompts or model parameters.
Its components are effectively:

- failure analyzer/proposer,
- skill builder,
- held-out evaluator,
- selection frontier/history.

A candidate skill survives only when evaluation justifies it.

Lesson for CTFH:
- make a Skill package a versioned artifact with its own evaluation evidence;
- skill promotion belongs in an experiment/evaluation pipeline, not the live worker loop.

### SkillWeaver

Primary repository:
- https://github.com/OSU-NLP-Group/SkillWeaver

SkillWeaver is especially relevant to the "hands" discussion. A web agent explores a website,
practices interactions, and synthesizes reusable APIs. The learned skill is not merely textual
memory; it becomes an executable action abstraction layered over browser interaction.

Lesson for CTFH:
- some skills can expose higher-level tool APIs backed by a Hand;
- browser interaction should support learned or hand-written macro actions without changing
  browser ownership.

### Agent Skills specification

Primary source:
- https://agentskills.io/specification
- https://github.com/agentskills/agentskills

Portable skill package:

```
skill-name/
├── SKILL.md
├── scripts/
├── references/
└── assets/
```

Important properties:

- metadata + instructions in SKILL.md,
- optional executable scripts and references,
- progressive disclosure,
- optional allowed-tools,
- model/harness chooses activation, then loads deeper resources only on demand.

Lesson for CTFH:
- use the standard format rather than inventing a CTF-specific skill document;
- add CTFH metadata/evals as compatible optional files rather than replacing SKILL.md;
- Skill Registry should load only catalog metadata on the fast path.

## 2. Hands and agent-computer interfaces

### SWE-agent and Agent-Computer Interface (ACI)

Primary source:
- https://arxiv.org/abs/2405.15793
- https://github.com/swe-agent/swe-agent

SWE-agent demonstrates that interface design can materially improve the same model. Its ACI
replaces noisy human-shaped interaction with agent-shaped commands and concise structured
feedback.

Key idea:
- a generic shell/editor is not automatically a good model interface;
- windowed output, error feedback, linter results, and compact search improve action quality.

Lesson for CTFH:
- TerminalHand should not expose only raw `bash -lc`;
- browser/terminal/debugger observations should have bounded structured views;
- raw output belongs in TraceStore/ArtifactStore, while the brain receives a context-safe view.

### EnIGMA

Primary source:
- https://proceedings.mlr.press/v267/abramovich25a.html

EnIGMA is directly CTF-relevant. Its major gain comes from **interactive agent tools** that make
debuggers and server connections usable while preserving an independent main shell. It evaluated
390 CTF challenges across several benchmarks.

Lesson for CTFH:
- interactive terminal sessions are a first-class capability, not just subprocess calls;
- PTY/GDB/netcat/server sessions need durable session IDs and non-blocking read/write APIs;
- CTF "hands" are broader than browser control.

### BrowserGym / AgentLab

Primary source:
- https://arxiv.org/abs/2412.05467

BrowserGym's useful split is:

- environment,
- observation space,
- action space,
- agent,
- benchmark/evaluation.

Lesson for CTFH:
- BrowserHand should explicitly expose observation and action contracts;
- browser benchmark/evaluation must stay separate from the browser session implementation.

### WebArena / WebVoyager

Primary sources:
- https://arxiv.org/abs/2307.13854
- https://aclanthology.org/2024.acl-long.371/

They demonstrate that browser observation can be DOM, accessibility tree, screenshot, or mixed,
and that no single representation is universally optimal.

Lesson:
- observation mode belongs to a BrowserHand profile;
- CTFH should not hard-code DOM-only or screenshot-only control.

### browser-use

Primary repository:
- https://github.com/browser-use/browser-use

Current structure separates concepts such as:

- Agent,
- BrowserProfile / BrowserSession,
- Tools,
- Skills,
- planning/recovery,
- message compaction.

The browser session owns browser lifecycle/state. Tools are injected separately.

Lesson:
- CTFH BrowserHand should own BrowserSession/Profile;
- tools are generated/bound from the active session;
- the Brain should never directly own the browser process.

### Agent-S / computer use agents

Primary repository:
- https://github.com/simular-ai/Agent-S

Agent-S variants separate a planning/manager role, an executor/worker, an Agent-Computer
Interface/grounding layer, and experience/knowledge memory.

Lesson:
- GUI grounding is an implementation detail of DesktopHand/Perception, not a kernel concern;
- optional planner/executor overlays may improve computer-use performance, but should remain
  Brain policies, not compulsory CTFH architecture.

## 3. Memory and knowledge

### MemGPT / Letta

Primary sources:
- https://docs.letta.com/

Useful separation:

- durable message/experience history,
- always-visible memory blocks,
- retrievable external memory,
- tools,
- persistent agent state.

Lesson for CTFH:
- "context currently visible to the model" is not the same as persistent memory;
- ContextCompiler should project selected memory into the brain;
- memory storage must remain available even after context compaction.

### A-MEM

Primary source:
- https://proceedings.neurips.cc/paper_files/paper/2025/hash/19909c36f51abc4856b4560aff3d36d6-Abstract-Conference.html

A-MEM dynamically creates linked notes and evolves contextual metadata rather than keeping only a
flat vector store.

Lesson:
- CTFH MemoryProvider should not assume one retrieval implementation;
- vector, graph, episodic/semantic, and simple filesystem/wiki backends should all fit the same
  interface.

### Procedural memory research

Recent procedural-memory work treats procedures as learned/updatable structures and separately
studies build, retrieval, update, correction, and deprecation.

Lesson:
- there is conceptual overlap between procedural memory and Skills;
- for CTFH's engineering boundary, portable executable/procedural artifacts should live in
  **Skills**, while Memory stores episodes/knowledge/evidence that can generate or select skills.

## 4. Runtime and kernel boundaries

### AIOS

Primary sources:
- https://arxiv.org/abs/2403.16971
- https://github.com/agiresearch/AIOS

AIOS moves scheduler, context, memory, storage, tools, and access control into a kernel-like
runtime. Its computer-use architecture adds VM and MCP integration.

Useful lesson:
- lifecycle/resource management belongs below agent applications.

Important caution:
- CTFH should not copy AIOS wholesale. CTFH's goal is a thin CTF supervisor, not a general
  multi-tenant agent operating system.

### AutoGen

Primary sources:
- https://microsoft.github.io/autogen/stable/

AutoGen distinguishes:

- Core runtime,
- higher-level AgentChat,
- Extensions.

The runtime manages identity, lifecycle, communication, security boundaries, and local/distributed
execution.

Lesson:
- CTFH can use the same layering principle without adopting a message-bus-first architecture.

### OpenHands

Primary sources:
- https://docs.openhands.dev/

OpenHands strongly separates:

- Agent,
- Runtime/Agent Server,
- Workspace/Sandbox,
- Actions and Observations,
- append-only events,
- tools/plugins.

Lesson:
- CTFH should represent execution events independently of LLM-visible message formatting;
- Hand/resource lifecycle belongs to runtime/workspace layers.

### LangGraph

Primary sources:
- https://docs.langchain.com/oss/python/langgraph/persistence

Useful distinction:

- checkpointer = thread/run state,
- store = durable cross-thread memory.

Lesson:
- StateStore recovery/checkpoint state and MemoryProvider knowledge must remain different modules.

## 5. Tool interoperability

### Model Context Protocol (MCP)

Primary sources:
- https://modelcontextprotocol.io/

MCP exposes tools, resources, and prompts through a host/client/server protocol and capability
negotiation. The 2026 specification emphasizes an interoperability boundary.

Lesson:
- MCP is an excellent **edge adapter** for external tools/services;
- internal CTFH hot-path interfaces should remain direct typed Python APIs;
- do not route every kernel operation through MCP simply for architectural uniformity.

## 6. The distinctions CTFH must preserve

### Brain vs Context

Brain:
- model/native agent adapter,
- planning/action selection,
- optional routing/critic policies.

Context:
- decides what information is projected into Brain input,
- does compaction/progressive disclosure,
- combines intent brief, skill instructions, tool schemas, memory retrieval, and observation.

A context optimizer is therefore not "memory" and not the brain.

### Hand vs Tool

Hand:
- owns a stateful external resource/session;
- has lifecycle and ownership;
- emits observations;
- may expose many tools.

Tool:
- atomic typed callable;
- no independent resource lifecycle is required;
- may be bound to a Hand session.

Examples:

- BrowserHand owns Chrome/GhostChrome profile/session.
- `browser.click`, `browser.type`, `browser.snapshot` are Tools bound to that Hand.
- TerminalHand owns PTY/process sessions.
- `terminal.exec`, `terminal.send`, `terminal.read` are Tools.
- A pure hash calculator is a stateless Tool and needs no Hand.

### Skill vs Tool

Tool says **what operation can be executed**.

Skill says **how and when to combine operations**.

A skill may include:
- instructions,
- scripts,
- reference docs,
- tool requirements,
- validation guidance.

A skill must not own a browser process or worker lifecycle.

### Memory vs Wiki vs Skill

Recommended taxonomy:

- Raw Trace: immutable evidence of what happened.
- Episodic Memory: task/run-specific remembered experience.
- Knowledge Wiki: consolidated semantic patterns and lessons.
- Skill: executable/procedural package used during action.
- Artifact: durable file/output produced by a task.

WikiSkill's separation is a strong basis for this boundary.

## 7. Design rules selected for CTFH V3

1. Keep the kernel thin.
2. Make Brain, Hand, Memory, SkillSource, ToolProvider, and Verifier replaceable.
3. Keep internal module calls direct; use MCP only at interoperability edges.
4. Use Agent Skills as the portable skill format.
5. Browser and terminal are stateful Hands, not generic Tools.
6. Every Hand session has owner, generation/epoch, reconcile, and teardown semantics.
7. Tool schemas are generated from the currently admitted capability set.
8. Skills are progressively disclosed and can declare tool/capability requirements.
9. Raw traces are immutable; knowledge/wiki and skills are derived artifacts.
10. Skill evolution runs outside the live solve fast path unless explicitly benchmarked.
11. ContextCompiler is a first-class module separate from Memory and Brain.
12. OutcomeAuthority remains independent and is the only path to SOLVED.
13. Any additional planner/router/memory/skill layer must earn promotion through matched A/B
    evidence.
14. Fast path must remain close to "native agent + ready environment + minimal capability set".
15. Browser/GUI observation formats are profile choices, not kernel assumptions.

## 8. Anti-patterns to avoid

- One giant Agent object owning model, browser, memory, tools, state, and evaluation.
- Treating every callable as a "tool" even when it owns a long-lived session.
- Treating skills as retrieved chat memories.
- Loading every skill body into every prompt.
- Letting a skill silently grant itself extra tools or network scope.
- Making MCP the internal kernel ABI.
- Evolving skills during competition without held-out validation.
- Letting browser cancellation destroy target state.
- Passing raw unbounded terminal/browser output directly into model context.
- Adding planner/executor/multi-agent routing as mandatory architecture before matched benchmarks.

## 9. Sources selected as architectural anchors

- WikiSkill — https://arxiv.org/abs/2608.27454
- Trace2Skill — https://arxiv.org/abs/2603.25158
- EvoSkill — https://arxiv.org/abs/2603.02766
- SkillWeaver — https://github.com/OSU-NLP-Group/SkillWeaver
- Agent Skills — https://agentskills.io/specification
- EnIGMA — https://proceedings.mlr.press/v267/abramovich25a.html
- SWE-agent ACI — https://arxiv.org/abs/2405.15793
- BrowserGym — https://arxiv.org/abs/2412.05467
- WebArena — https://arxiv.org/abs/2307.13854
- WebVoyager — https://aclanthology.org/2024.acl-long.371/
- browser-use — https://github.com/browser-use/browser-use
- Agent-S — https://github.com/simular-ai/Agent-S
- UI-TARS — https://github.com/bytedance/UI-TARS
- A-MEM — https://proceedings.neurips.cc/paper_files/paper/2025/hash/19909c36f51abc4856b4560aff3d36d6-Abstract-Conference.html
- Letta — https://docs.letta.com/
- LangGraph persistence — https://docs.langchain.com/oss/python/langgraph/persistence
- AutoGen runtime — https://microsoft.github.io/autogen/stable/
- OpenHands — https://docs.openhands.dev/
- AIOS — https://github.com/agiresearch/AIOS
- OS-Copilot — https://github.com/OS-Copilot/OS-Copilot
- MCP — https://modelcontextprotocol.io/

## 10. Cross-check against current production agent SDKs

The module boundary above was cross-checked against several current production-oriented SDKs.
The point is not to copy their orchestration features, but to see which separations recur across
independent implementations.

### OpenAI Agents / Agents API

Current OpenAI agent surfaces separate an Agent definition from its Environment, durable Session,
and streamed Events/Items. The Agents SDK adds reusable tools, sessions, guardrails, tracing and
handoffs around a runner loop; the lower-level Responses API leaves the loop to the application.
This supports CTFH keeping Brain definition distinct from execution environment/session ownership
and trace events.

Primary sources:
- https://developers.openai.com/api/docs/guides/agents
- https://github.com/openai/openai-agents-python

### Google ADK

Google ADK separates Runner, Agents, Tools/Callbacks, Events, and persistence Services. In
particular, SessionService, ArtifactService and MemoryService are different interfaces, and the
Runner commits event-indicated state through those services. This independently reinforces three
CTFH decisions:

- runtime/checkpoint state is not long-term memory;
- artifacts are not memory;
- execution facts should be events independent of model-visible context.

Primary sources:
- https://github.com/google/adk-docs/blob/main/docs/runtime/event-loop.md
- https://adk.dev/sessions/memory/
- https://adk.dev/artifacts/

### PydanticAI

PydanticAI exposes agents, composable toolsets/capabilities, model providers, typed outputs and
validation, while its durable-execution layer treats model requests, tool discovery, argument
validation and tool calls as separately journalable operations. Its durability documentation also
warns that live workspace/tool objects do not automatically cross durable boundaries; references
or snapshots do. This aligns with CTFH's resource-ref + Hand-lease model.

Primary sources:
- https://pydantic.dev/docs/ai/
- https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/

### Cross-framework invariant

Across WikiSkill/Trace2Skill, browser-use/BrowserGym, EnIGMA/SWE-agent, OpenHands, AutoGen,
LangGraph, OpenAI Agents, Google ADK and PydanticAI, the recurring separation is:

- decision logic,
- execution interface/resources,
- typed actions/tools,
- durable run/session state,
- long-term knowledge/memory,
- portable procedural knowledge/skills,
- immutable or durable execution evidence,
- independent evaluation/validation.

CTFH V3 therefore treats this separation as the default architectural hypothesis to benchmark,
not as a framework-specific convention.
