# Architecture Plan: Multi-language Optimization and Project Refactoring

**Status:** target architecture. A first host-side snapshot/PatchSet foundation is implemented in `src/metaforge/projects`; it is not yet connected to agents, project builds/tests, or automatic refactoring.

## 1. Current boundary

The current source-level evolution engine is a controlled experiment for Python integer sorting. It generates one pure `solve(values)` function, validates it against a fixed contract, reviews it, runs it in a rootless container, and ranks it over a deterministic workload suite. The C and Rust adapters currently execute trusted repository-owned implementations.

The repository now also contains a host-side `ProjectSnapshot` and `ProjectPatch` contract. It can capture an allow-listed UTF-8 source/documentation manifest and atomically apply hash-preconditioned create/replace/delete operations into a new disposable directory. It never modifies the source checkout and does not execute candidate code. It is not integrated with a model, compiler, project test runner, or full project-refactoring agent.

Do not remove this boundary until the replacement has equivalent or stronger isolation and validation.

## 2. Target architecture

The next architecture should separate the search engine from any single problem, language, compiler, or project layout.

```text
                 Experiment / Refactoring Request
                              |
                       Problem Contract
             objective + constraints + acceptance gates
                              |
                 Immutable Project / Input Snapshot
                 manifest + content hashes + provenance
                              |
                  Analysis and Hypothesis Planning
           dependency graph + architecture metrics + risks
                              |
                 Bounded Specialist-Agent Proposals
             plans, candidate descriptors, or PatchSets
                              |
                    Deterministic Policy Gate
           schema + path policy + hashes + static security
                              |
                Language-Specific Build / Run Adapters
           Python | Rust | C | future languages as needed
                              |
                   Isolated Evaluation Environment
        no network + no secrets + resource limits + pinned image
                              |
                Immutable Baseline Acceptance Tests
             plus candidate tests + property/fuzz tests
                              |
            Correctness + Security + Performance + Quality
                              |
                    Pareto / Threshold Selection
                              |
                 Evidence, Lineage, and Reproducible Replay
                              |
                Reviewable Patch / Release Candidate
```

No agent should directly edit the working repository, run arbitrary host commands, or decide that its own output passed. Agents return bounded data; deterministic host code applies policy and orchestrates evaluation.

## 3. Core contracts

### ProblemSpec

Introduce a versioned, language-neutral problem contract with:
- A stable problem identifier and schema version.
- Objective, input/output schemas, semantic invariants, and constraints.
- Workload generation and independent correctness oracles.
- Required validation gates and resource budgets.
- Explicit performance objectives and acceptable regression thresholds.

The current integer-sort task becomes one implementation of this contract, not a special case embedded throughout the orchestration engine.

### ProjectSnapshot

The current initial implementation provides:
- Deterministic manifest for allow-listed UTF-8 text files under `src/`, `tests/`, `docs/`, and `backends/`, plus selected root documentation.
- Per-file size and SHA-256, total-size/count budgets, canonical manifest fingerprint, case-collision checks, Unicode NFC paths, and no-follow file access where the platform provides descriptor-relative APIs.
- Symlink and special-file rejection in included roots; hidden paths, common environments, caches, build outputs, binary files, and dependency/build manifests are excluded or protected.
- Default budgets: 1,000 files / 20 MiB snapshot; 30 patch changes / 256 KiB per changed file / 1 MiB total patch content.
- The current snapshot does **not** scan for hardcoded credentials in included source files. Project files must not be sent to a model until secret scanning/redaction is implemented.
- Binary assets are not included, so this initial disposable copy is a text-source workspace, not a complete runnable clone of arbitrary projects.

The next step is a richer immutable project snapshot with explicit baseline test identity and secret scanning before any external model context is assembled.

### PatchSet

Project-level agents must return structured file operations, not unrestricted shell scripts:
- Each operation names a normalized relative path and one of `create`, `replace`, or `delete`.
- Replacements/deletions carry the expected pre-change SHA-256; creations require the path not to exist.
- The implemented parser rejects unknown fields, path traversal, absolute/drive/UNC paths, hidden paths, Windows reserved device names, Unicode non-NFC/control paths, case-colliding paths, protected build/dependency manifests, scripts, and unapproved file types.
- Existing files under `tests/` are immutable; only new test files may be added.
- Current limits are enforced before file materialization. Source snapshots are revalidated before copy; every copied source file is checked against its captured hash.
- Patch application uses a temporary sibling directory and atomic rename to a destination that must not already exist. The original checkout is never modified and no candidate code, build or tests are run.
- This PatchSet foundation is currently host-side only; model generation and isolated project testing remain future milestones.

### LanguageBackend

The backend contract should expose preparation, build, run, repeated measurements, artifact hashes, compiler/runtime versions, build flags, and typed errors. Each supported language gets a separately versioned and tested adapter.

Initial priority:
- **Python:** orchestration, analysis adapters, rapid prototyping, Python project refactors.
- **Rust:** memory-safe systems components, compiled candidate execution, high-performance core services.
- **C:** constrained interoperability and performance baselines; stricter compiler/static-analysis gates.
- **Other languages:** add Go, TypeScript, JVM, or other backends only when a target project requires them. More languages are not automatically better.

Generated native source must be compiled and run inside a pinned, no-network sandbox image. It must never be compiled on the host as a convenience path.

## 4. Project-refactoring evaluation pipeline

A project refactor must pass these gates in order:

1. **Baseline capture:** snapshot the repository, lockfile, toolchain versions, existing tests, static-analysis output, architecture metrics, and benchmark results.
2. **Baseline validation:** run the trusted acceptance suite before proposing changes. A failing baseline blocks optimization or marks the known failures explicitly.
3. **Analysis:** derive an import/dependency graph, module boundaries, circular dependencies, complexity/coupling metrics, security findings, and performance hotspots.
4. **Hypotheses:** require each proposed change to state the problem, expected benefit, affected modules, risk, and falsifiable acceptance tests.
5. **Patch validation:** validate schema, file paths, expected hashes, protected paths, size/count limits, and syntax before building.
6. **Static gates:** language-specific parsers, formatters, linters, type checkers, dependency policy, secret scanning, and security analyzers.
7. **Build and test:** no-network build using pinned toolchains; run immutable baseline acceptance tests against the patched source; then run candidate-added tests separately.
8. **Adversarial validation:** property-based, fuzz, boundary, mutation, malformed-input, resource-exhaustion, and regression tests proportionate to the changed surface.
9. **Performance evaluation:** paired baseline/candidate measurements on representative workload profiles, warmups, multiple rounds, raw samples, robust statistics, and minimum practical effect thresholds.
10. **Architecture evaluation:** check dependency direction, cyclic imports, module cohesion/coupling, public API compatibility, complexity, and testability. Do not collapse these into one opaque score.
11. **Selection:** reject any patch with a correctness, security, compatibility, or required-quality regression. Use Pareto selection only among candidates that satisfy mandatory gates.
12. **Evidence and delivery:** archive the snapshot hashes, PatchSet, toolchain/image digests, commands, tests, findings, metrics, raw samples, lineage, and replay instructions. Return a reviewable diff; applying or merging it remains an explicit release decision.

The evaluator must never trust test results produced only by tests that the candidate itself was allowed to rewrite. The immutable baseline suite is the acceptance authority.

## 5. Optimization criteria

"Optimal" is always relative to a defined objective and workload distribution. MetaForge should not report a global optimum unless the search space is finite and exhaustively covered with a proof.

Use hard gates for correctness, security, API compatibility, and resource limits. Among candidates that pass, report a Pareto frontier across explicitly requested objectives such as:
- Runtime latency / throughput.
- Peak memory and allocation volume.
- Build size and startup cost.
- Reliability and robustness across workload classes.
- Complexity, dependency coupling, and maintainability.
- Security findings and attack surface.

A candidate is not an improvement when a small timing win is accompanied by a correctness, security, reliability, or maintainability regression.

## 6. Specialist roles and trust boundaries

Useful roles include:
- **Planner:** decomposes the request into explicit hypotheses and acceptance criteria.
- **Architect:** analyzes module boundaries, dependencies, and alternative designs.
- **Implementer:** returns a structured PatchSet for one bounded hypothesis.
- **Security reviewer:** analyzes attack surface and threats; its opinion is advisory until deterministic policy gates pass.
- **Test designer:** proposes boundary, property, fuzz, and regression cases.
- **Performance analyst:** interprets raw measurements and uncertainty without changing acceptance rules.

Agents may disagree and propose alternatives. No agent can approve its own patch, edit immutable tests, waive a mandatory gate, or modify the evaluator policy.

## 7. Required security properties

- No host execution of untrusted generated source.
- No network or ambient credentials in candidate/build/test containers.
- Digest-pinned images and reproducible toolchains.
- Rootless isolation, read-only source mounts, bounded writable scratch space, resource limits, timeouts, and bounded outputs.
- No automatic dependency downloads during evaluation.
- Strict path and symlink validation for every patch operation.
- Immutable acceptance tests and independent correctness oracles.
- Fail-closed behavior if any required control or measurement is unavailable.
- A disposable VM or dedicated worker for high-risk projects; container isolation alone is not a formal host-security guarantee.

## 8. Delivery milestones

- **V0.8:** harden AST review and introduce reproducible multi-workload algorithm evaluation. (The two increments are currently in review/merged as noted in the repository changelog.)
- **V0.9:** extract ProblemSpec and LanguageBackend contracts; add typed experiment plans and backend capability discovery.
- **V0.10 foundation (initial implementation):** ProjectSnapshot, PatchSet schema, path/hash policy, and safe patch application into a disposable copy are implemented with adversarial tests. Secret scanning, binary/resource preservation, model integration, and project-test execution remain pending.
- **V0.11:** implement one end-to-end Python project-refactoring backend using immutable tests, secret scanning, no-network sandbox builds, baseline comparisons, and patch replay.
- **V0.12:** add Rust/C generated-source backends in pinned compiler images, with offline compilation and differential/fuzz testing.
- **V1.0:** enable broader multi-language and architecture-level optimization only after the same acceptance and threat-model requirements are met across backends.

Each milestone requires passing CI on supported Python versions, static analysis, adversarial tests, and a live isolated integration job before integration into `main`.
