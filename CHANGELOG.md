# Changelog

## 0.2.0 — 2026-11-22

- Added deterministic specialist-agent contracts, registry, workflow, and built-in Candidate Designer, Safety Reviewer, and Benchmark Analyst.
- Enforced safety review as a mandatory pre-execution gate.
- Added optional LangGraph orchestration adapter.
- Added bounded parallel evaluation, global wall-clock budget, and maximum evaluation count.
- Added environment metadata, canonical dataset SHA-256, UTC timestamps, and run identifiers.
- Added candidate lineage with parent fingerprints, mutation/selection origin, and generation.
- Added immutable run archives and replay of synthetic CLI experiments.
- Expanded Markdown reports and security documentation.
- Added CI coverage for Python 3.11, 3.12, 3.13, optional LangGraph, and CLI smoke tests.

### Security limitations

MetaForge still executes only package-owned allow-listed implementations. The worker process is not a hardened sandbox and must not execute untrusted generated source.

## 0.3.0 — 2026-11-22

- Added heapsort and shellsort implementations.
- Added hybrid quicksort with configurable insertion-sort cutoff.
- Expanded the allow-listed candidate schema and seeded search space.
- Added seeded crossover and expanded mutation operators.
- Added non-dominated Pareto ranking and crowding-distance selection over runtime and traced allocation peak.
- Added strategy-family diversity preservation during parent selection.
- Preserved raw timing/memory samples and runtime median absolute deviation.
- Added Pareto-front reporting.
- Persisted the exact candidate population and lineage for every generation.
- Added schedule-based replay so candidate proposals are reproducible even when fresh timing measurements would change selection.
- Added deterministic differential tests across the candidate search space.

### Security limitations

All candidate strategies remain package-owned allow-listed Python functions. The worker process is not a hardened sandbox and does not execute model-generated code.

## 0.4.0 — 2026-11-22

- Added an optional OpenAI Agents SDK Candidate Designer adapter.
- Added strict Pydantic structured output for bounded candidate proposals.
- Disabled agent tools and handoffs; the model is limited to one turn and a per-call timeout.
- Disabled sensitive trace payloads for MetaForge LLM proposal runs.
- Required proposals to match the exact registered candidate search space.
- Added deterministic fallback when model output is invalid, incomplete, unavailable, or fails.
- Added CLI opt-in through --llm-proposals and explicit model/API-key environment configuration.
- Added offline ScriptedModel integration tests; no live API key is needed for CI.
- Kept LLM proposals restricted to candidate descriptors. No model-generated source is compiled or executed.

### Live-provider validation

The provider integration is covered with the Agents SDK's scripted model. A real provider request still requires the user's own API key and model access; credentials are never stored in the repository or experiment artifacts.

## 0.5.0 — 2026-11-22

- Added a versioned execution-backend protocol and Python adapter.
- Added trusted Rust and C11 backends for built-in sort, heapsort, and shellsort.
- Rust compilation is dependency-free and runs Cargo in offline, locked mode.
- Native backend execution uses minimal child-process environments and bounded input/time limits.
- Recorded compiler/runtime versions, build flags, source hashes, binary hashes, and compilation duration separately from algorithm runtime.
- Added backend-check CLI command to compare outputs against Python's independent sorting oracle.
- Added CI compilation and differential correctness tests for both Rust and C backends.
- Kept native peak-memory metrics explicitly unavailable rather than presenting incomparable measurements as equivalent.

### Security boundary

Only fixed source files shipped in the repository are compiled. Candidate or LLM-generated source is not accepted by native backend compilers.
