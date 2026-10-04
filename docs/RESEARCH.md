# CTFH v2 — Research Notes

Date: 2026-10-04

This document records the primary research and open-source systems used to shape
CTFH v2. Claims are intentionally scoped to what each source actually measured.

## 1. Systematic Capability Benchmarking of Frontier LLMs for Offensive Cyber Tasks

Paper:
- https://arxiv.org/html/2604.17159

Repository:
- https://github.com/TATAR-LAB/ctf-agents

Important results:
- full 200-task NYU CTF Bench evaluation
- matched Gemini 3 Pro environment result:
  - Kali: 104/200 = 52.0%
  - Ubuntu: 85/200 = 42.5%
- tested planner/executor pairings:
  - Pro/Pro: 52%
  - Pro/Flash: 28.5%
  - Flash/Flash: 27%
  - Flash/Pro: 23.5%
- auto-prompt/category hints can hurt in a rich tool environment

Decision:
- environment/tooling belongs in the measured runtime profile
- do not assume strong-planner/cheap-executor is good
- do not generalize these Gemini-specific ablations to every future model

## 2. EnIGMA

Paper:
- https://proceedings.mlr.press/v267/abramovich25a.html
- https://arxiv.org/abs/2409.16165

Key result:
- interactive terminal tools, debuggers and server-connection interfaces materially
  improve CTF-agent performance
- evaluated 390 challenges across four benchmarks
- identifies "soliloquizing": agents inventing observations without environment
  interaction

Decision:
- PTY/interactive environment quality is a first-class concern
- environment observations and authoritative verification beat worker self-report
- do not infer that a generic tool-plugin framework is necessary

## 3. D-CIPHER

Paper:
- https://arxiv.org/html/2502.10931v1

Repository:
- https://github.com/NYU-LLM-CTF/nyuctf_agents

Architecture:
- planner
- fresh executor context per delegated subtask
- optional auto-prompter
- shared execution environment

Reported benchmark results:
- 22.0% NYU CTF Bench
- 22.5% CyBench
- 44.0% HackTheBox

Important ablation lesson:
- planning/fresh contexts can help, but gains are model-dependent
- auto-prompting can hurt
- weak executor substitutions can reduce performance

Decision:
- planner/executor is an optional later Strategy, not the foundation

## 4. DeepRed

Paper:
- https://arxiv.org/html/2604.19354

Repository:
- https://github.com/AISE-TUDelft/DeepRed-LLMAgent

Ideas:
- realistic isolated execution
- full trajectories
- checkpoint-based partial-credit evaluation
- summarise-then-judge evaluation pipeline

Decision:
- progress/checkpoint information is useful offline
- hidden rubrics must remain outside live worker context
- runtime progress must come from observable evidence, not leaked evaluator state

## 5. AgentCyberRange / CAGE

Paper:
- https://arxiv.org/html/2606.14295v2

Repository:
- https://github.com/AgentCyberRange/CAGE

Key architecture:
- heterogeneous agent adapters
- agent manager for model endpoints and traces
- benchmark manager for deployment/instances
- independent verifier/scorer
- isolated execution

Important implication:
- deployed system = native agent harness + model + environment + evaluator
- model name alone is not the benchmark unit

Decision:
- AgentAdapter and environment identity are first-class
- native Codex/Claude behavior should be preserved rather than recreated in a
  lowest-common-denominator custom ReAct loop

## 6. CTF-Dojo / CTF-Forge

Paper:
- https://arxiv.org/html/2508.18370

Repository:
- https://github.com/amazon-science/CTF-Dojo

Key facts:
- 658 executable Dockerized CTF challenges
- CTF-Forge automates container/environment construction
- execution-grounded trajectory training on verified challenges reported up to
  +11.6 percentage-point improvement over base models in that work

Decision:
- execution-ready environments and reproducible target lifecycle are a major
  infrastructure investment
- environment generation and target generation remain distinct from agent runtime

## 7. CyBench

Project:
- https://cybench.github.io/

Paper:
- https://arxiv.org/abs/2408.08926

Key lesson:
- professional-level CTF benchmark
- pseudoterminal scaffolding improved some Claude 3.5 Sonnet settings; effects
  varied by prompt/model

Decision:
- PTY is a capability to benchmark, not a universal prompt trick

## 8. Autonomous LLM Agents & CTFs: A Second Look

Paper:
- http://arxiv.org/abs/2605.21497v1

Results on 30 web CTFs:
- GPT-5 Executor: 19/30
- GPT-5 Executor+Evaluator: 19/30
- GPT-5 Planner+Executor+Evaluator: 19/30
- Claude Code: 19/30

Reported average durations:
- Executor: ~337 s
- Executor+Evaluator: ~799 s
- Planner+Executor+Evaluator: ~926 s

The structured systems reduced steps/cost, but did not increase peak coverage and
greatly increased elapsed time in that experiment.

The paper also found environment limitations such as missing browser rendering
could directly block challenge categories.

Decision:
- competition optimization must treat wall-clock as first-class
- extra orchestration has to beat direct native-agent baseline on deadline score,
  not just step count or cost
- vulnerability/task recognition may be a more important bottleneck than adding
  more executor layers

## 9. AgentCTF x AgentXploit

Site:
- https://ctf.secure-agent.com/

Repository:
- https://github.com/agentbeats/agentctf

Competition conditions:
- public development set + hidden test set
- $10 model budget per task
- 15-minute task limit
- official rerun verification

Report/search findings:
- top systems benefited from strong foundation models and task-adaptive domain
  context
- task-ID hardcoding did not generalize to hidden tasks
- more complicated architectures were not automatically better

Decision:
- hidden tasks and domain-general skills are more meaningful than optimizing to
  known challenge IDs
- keep direct native-agent baseline

## 10. CTFusion

Paper:
- https://arxiv.org/html/2605.11504v2

Repository:
- https://github.com/kaist-hacking/CTFusion

Purpose:
- live CTF streaming evaluation through CTFd/MCP
- isolate per-agent progress while sharing a team account
- forward only first correct flag to reduce competition impact

Leakage findings:
- static NYU CTF performance was materially higher than live-CTF performance
- web-search enabled agents retrieved public flags/writeups
- 71 logged cheating attempts were identified in their analysis
- no-cheat controls reduced apparent success

Decision:
- static benchmarks are regression tools, not final promotion evidence
- use fresh/live/private holdouts for major profile decisions
- isolate browser state, native memory, prior trajectories, indexes and evaluator
  secrets between trials

## 11. Agent Skills

Specification:
- https://agentskills.io/specification

Key design:
- SKILL.md
- scripts/
- references/
- assets/
- progressive disclosure:
  - metadata at startup
  - instructions when activated
  - resources on demand

Decision:
- use the format for portable technique/workflow knowledge
- skills are AgentAdapter/profile configuration, not v0 kernel objects

## 12. MCP

Specification:
- https://modelcontextprotocol.io/specification/2026-07-28/architecture/index

Key design:
- host/client/server boundaries
- capability negotiation
- tools/resources/prompts
- asynchronous tasks/skills extensions

Decision:
- use MCP when external interoperability is valuable
- do not route kernel-internal hot-path calls through MCP merely for abstraction

## 13. Jev Ultrafast

Repository:
- https://github.com/browser-use/jev-ultrafast

Architecture:
- indexed dynamic browser action space
- Jev chooses operation and compatible target
- small text model only when text generation is required

Published example:
- Google Flights run around 7.1 seconds
- six alternating runs: median 9.450 s -> 7.092 s
- protocol calls 1092 -> 101
- this is a small single-task measurement, not a general reliability benchmark

Decision:
- promising future BrowserDecisionPolicy
- do not let Jev own a second browser when an existing managed browser owns auth
  and session state

## 14. context-mode

Repository:
- https://github.com/mksglu/context-mode

Claims/examples:
- large tool output sandboxing
- FTS5-based session/context retrieval
- project shows examples near 98% context reduction for large outputs

Decision:
- optional later context view
- avoid double-compaction with native agent context management
- benchmark it per native agent

## 15. codebase-memory-mcp

Repository:
- https://github.com/DeusData/codebase-memory-mcp

Paper/repo claims:
- persistent structural code graph
- evaluated across real repositories
- fewer tokens/tool calls than file-by-file exploration in their study

Decision:
- enable only for sufficiently large source challenges
- not a v0 dependency

## 16. RTK

Repository:
- https://github.com/rtk-ai/rtk

Claims:
- terminal output compaction
- project reports ~60-90% reductions on common dev-command output with low
  overhead

Security note:
- 2026 advisories affected command-rewrite/permission integration paths

Decision:
- explicit output compaction view only in CTFH
- never use RTK rewrite/auto-allow behavior as the execution authorization layer
- WorkerEnvironment/kernel owns permission and sandbox policy

## 17. LiteLLM

Repository:
- https://github.com/BerriAI/litellm

Docs:
- https://docs.litellm.ai/

Useful features:
- provider normalization
- retries/failover
- routing primitives
- spend/usage accounting

Decision:
- optional inside custom API AgentAdapter
- not the CTF-specific scheduler/router
- native Codex/Claude adapters should stay native

## 18. Python plugin mechanisms

pluggy:
- https://github.com/pytest-dev/pluggy
- https://pluggy.readthedocs.io/en/stable/

Decision:
- Python entry points and Protocols are sufficient once real third-party
  implementations exist
- do not build the plugin ecosystem in v0
- no generic hook/event fan-out on the hot path

## Final research conclusion

The highest-confidence performance order is:

1. strong native agent baseline
2. ready isolated security-tool environment
3. authoritative result verification
4. process supervision and leak-free cancellation
5. precise latency measurement
6. environment warm-up / copy-on-write optimization
7. rate-limit-aware concurrency
8. pass@k / hedged pass@k
9. selective skills/context optimizers
10. planner/executor, Jev, structural code memory, learned routing only after
    paired benchmark wins

The architecture should remain thin until measurements justify each layer.
