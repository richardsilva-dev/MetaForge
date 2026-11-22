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
