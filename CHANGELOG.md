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
