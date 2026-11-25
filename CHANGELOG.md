# Changelog

## Unreleased

### Project snapshot and PatchSet foundation

- Added a deterministic project snapshot manifest over allow-listed UTF-8 text source/documentation paths, including SHA-256 per file and a canonical manifest fingerprint.
- Added strict versioned PatchSet contracts with create/replace/delete operations, per-file and total byte limits, duplicate/case-collision checks, Unicode/path normalization, Windows reserved-name checks, and traversal rejection.
- Protected CI/hidden paths, dependency and build manifests, lockfiles, shell/batch scripts, and existing acceptance tests; new test files may be added without altering baseline tests.
- Added no-follow file reading, symlink/special-file rejection, bounded snapshot sizes/counts, source re-snapshot checks, and per-file hash preconditions.
- Added atomic application into a new disposable copy only; source checkout is never modified and this API does not execute builds or tests.
- Added multilingual source file types for PatchSet representation; no generated native code compiler or model-driven project-refactoring workflow is wired yet.

### Benchmark robustness

- Added a versioned deterministic integer-sort workload suite covering original, ascending, descending, nearly-sorted, duplicate-heavy, low-cardinality, organ-pipe, and all-equal distributions.
- Require every candidate to pass the independent correctness oracle and benchmark gate on every workload before it can enter selection.
- Rank candidates by the median of per-workload medians with equal workload weighting; retain per-workload raw samples and container-wall measurements separately.
- Include suite version and per-workload SHA-256 fingerprints in experiment artifacts.
- Extend replay to reconstruct and verify the exact workload schedule without model calls; preserve replay compatibility with V0.7 single-input archives.
- Update live rootless sandbox integration tests to execute the full suite.

### Security hardening

- Reject wildcard imports in source-level candidate proposals so imported names cannot bypass static policy checks.
- Block `operator.attrgetter` and `operator.methodcaller`, including aliased module access and direct imports, to close reflection paths that bypass direct dunder-attribute checks.
- Bound AST traversal to 20,000 nodes and cap collected review findings to keep adversarial proposals from causing unbounded reviewer work or oversized reports.
- Add adversarial regression coverage for module aliases, imported reflection helpers, wildcard imports, AST complexity, and finding-volume bounds.
- Clarify the current security boundary and distinguish the existing Python sorting evolution path from future repository-wide refactoring.

### Scope and limitations

- Current source-level evolution remains a pure Python integer-sorting experiment. C/Rust support remains limited to trusted repository-owned implementations.
- General multi-language generated-code evaluation and multi-file architecture refactoring remain roadmap work; they require separate patch application, project test contracts, build isolation, and acceptance criteria.

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
- Expanded the allow-listed candidate schema and seeded search space.
- Added seeded crossover and expanded mutation operators.
- Added non-dominated Pareto ranking and crowding-distance selection over runtime and traced allocation peak.
- Added strategy-family diversity preservation during parent selection.
- Preserved raw timing/memory samples and runtime median absolute deviation.
- Added correctness gate against independent sorted() oracle.
- Persisted exact candidate populations and lineage for every generation.
- Added deterministic differential tests across candidate search space.

### Security limitations

All candidate strategies remain package-owned allow-listed Python functions. The worker process is not a hardened sandbox and does not execute model-generated source.

## 0.4.0 — 2026-11-22

- Added optional OpenAI Agents SDK Candidate Designer adapter.
- Added strict Pydantic structured output for bounded candidate proposals.
- Disabled agent tools and handoffs; model limited to one turn and per-call timeout.
- Disabled sensitive trace payloads for MetaForge LLM proposal runs.
- Required proposals to match exact registered candidate search space.
- Added deterministic fallback when model output is invalid, incomplete, unavailable, or fails.
- Added CLI opt-in through --llm-proposals and explicit model/API-key environment configuration.
- Added offline ScriptedModel integration tests.
- Kept LLM proposals restricted to candidate descriptors. No model-generated source was compiled or executed.

## 0.5.0 — 2026-11-22

- Added versioned execution-backend protocol and Python adapter.
- Added trusted Rust and C11 backends for built-in sort, heapsort, and shellsort.
- Rust compilation is dependency-free and runs Cargo offline and locked.
- Native backend execution uses minimal child-process environments and bounded input/time limits.
- Recorded compiler/runtime versions, build flags, source hashes, binary hashes, and compilation duration separately from algorithm runtime.
- Added backend-check CLI command and differential correctness tests for C/Rust.

### Security boundary

Only fixed source files shipped in the repository are compiled. Candidate or LLM-generated native source is not accepted.

## 0.6.0 — 2026-11-22

- Added fail-closed rootless container runner for Python candidate source.
- Required immutable SHA-256 image references and local image availability; image pulls are never performed by the runner.
- Added read-only candidate/input workspace, non-root UID, disabled networking, read-only rootfs, dropped capabilities, no-new-privileges, CPU/memory/PID/swap limits, bounded tmpfs/shared memory, process/file limits, bounded output streams, and container teardown on timeout/overflow.
- Added preflight and trusted sandbox-smoke CLI commands.
- Added live rootless Podman CI using pinned official Python 3.12 slim Bookworm image.
- The live smoke verifies mount flags, cgroup limits, no-network behavior, non-root identity, privilege/capability restrictions, file limits, and absence of host API credentials.
- Kept source-level generated-code execution disconnected from the descriptor-only evolution engine until the separate code-generation workflow.

### Validation

CI covered Python 3.11, 3.12, and 3.13; optional LangGraph/OpenAI Agents SDK; property tests; C/Rust build, lint, static analysis, differential backend checks; and rootless Podman smoke.

## 0.7.0-dev — 2026-11-25

- Added OpenAI Agents SDK adapter for bounded structured Python source proposals.
- Added conservative AST review, source fingerprinting, generation lineage, duplicate detection, and strict source-size limits.
- Added source-level evolutionary selection with a host-side independent correctness oracle.
- Added fail-closed rootless Podman/Docker execution using a digest-pinned image.
- Added algorithm-internal repeated timing separated from container startup wall time.
- Added code replay for archived candidates with dataset/source-integrity checks and no model calls.
- Added CI end-to-end scripted agent-to-sandbox integration and rootless smoke.
