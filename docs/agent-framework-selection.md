# Specialist-agent integration strategy (V0.2)

MetaForge must not delegate correctness, safety approval, resource budgets, or candidate promotion to an LLM. Those remain deterministic gates in the core engine. Agents may propose candidate configurations, inspect artifacts, plan tests, and interpret measured results. Their outputs are untrusted input and must be validated.

## Existing frameworks reviewed

- **LangGraph** provides a low-level graph runtime for stateful agent workflows and supports mixing deterministic nodes with model-driven nodes. This is a strong fit for explicit gated orchestration: https://docs.langchain.com/oss/python/langgraph/overview
- **Microsoft Agent Framework** combines AutoGen-style agent abstractions with Semantic Kernel capabilities and graph-based workflows. It offers sequential, concurrent, handoff, group-chat, and manager-led orchestration: https://learn.microsoft.com/agent-framework/overview
- **CrewAI** provides role-based agents, collaborative crews, and stateful Flows. Its Flow-first approach is useful when high-level agent collaboration is the main objective: https://docs.crewai.com/

## Decision

Keep the MetaForge core framework-neutral. Use a small typed adapter contract and explicit registry so third-party agents can be plugged in without making the experiment engine depend on one vendor or runtime. The first optional orchestration adapter should target LangGraph because its graph model can preserve deterministic gates and explicit state transitions. Microsoft Agent Framework and CrewAI remain viable adapters, not mandatory core dependencies.

The current built-in specialists are deterministic reference agents used to test contracts and workflow behavior without API keys. They are not represented as LLM agents. A future adapter may call external agent runtimes, but must return schema-validated data only.

## Trust boundaries

1. Agent output is untrusted data.
2. Candidate descriptors must pass schema and allow-list validation.
3. Safety review is advisory; the deterministic policy gate is authoritative.
4. Tests and independent correctness oracles determine validity.
5. Benchmark ranking is computed from measurements, not agent opinion.
6. No generated source is executed until a hardened sandbox is available.
