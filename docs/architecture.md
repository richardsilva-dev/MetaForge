# Architecture

## Experiment lifecycle

1. Validate the problem input and experiment configuration.
2. Ask the registered Candidate Designer for a bounded initial population.
3. Convert every proposal into a strict declarative Candidate.
4. Require the Candidate Safety Reviewer to approve the descriptor before execution.
5. Schedule evaluations under global time, evaluation-count, and concurrency budgets.
6. Execute only trusted allow-listed implementations in worker processes.
7. Compare outputs with an independent correctness oracle; incorrect candidates cannot be ranked.
8. Measure repeated runtime and traced Python memory.
9. Ask the Benchmark Analyst for descriptive summaries.
10. Select valid candidates using non-dominated sorting and crowding distance across runtime and traced allocation peak; preserve algorithm-family diversity, carry elites, create children through crossover/mutation, and record all parent fingerprints.
11. Persist run identity, dataset digest, environment metadata, configuration, per-generation results, and Markdown summary.

## Agent architecture

The registry is explicit and does not dynamically import arbitrary agent classes. The built-in specialists are deterministic reference implementations. The optional LangGraph adapter orchestrates registered roles without changing their permissions or bypassing deterministic gates. The optional OpenAI Agents SDK adapter can propose the initial descriptor population through strict Pydantic output, with no tools or handoffs, one turn, a per-call timeout, and trace payloads disabled. Proposals are accepted only if they exactly match the registered candidate search space; deterministic fallback fills missing proposals. The source-level CodeEvolutionEngine uses a separate code designer with strict structured output, no tools/handoffs, static AST review, a global call/time budget, and the same fail-closed sandbox. All model output remains untrusted. CodeReplayEngine re-evaluates archived source candidates without model calls after verifying the embedded dataset digest and every candidate source fingerprint.

## Candidate identity and lineage

A descriptor candidate fingerprint identifies its declarative algorithm parameters. A source candidate fingerprint is SHA-256 of the exact source bytes. Both are distinct from lineage: lineage records generation, operator, origin, and parent fingerprint. Identical implementations can therefore be recognized without losing the history of how they entered a generation.

## Reproducibility

Each run records a unique ID, UTC timestamps, seed, SHA-256 of the canonical dataset encoding, Python/runtime/platform metadata, source revision when supplied by the environment, and experiment configuration. CLI runs preserve their synthetic input dataset in the archive. Every generation stores its exact candidate population and lineage. Replay consumes that stored schedule rather than re-running timing-dependent selection, so the candidate sequence is reproducible. Exact timing reproduction is not promised.

## Execution boundary

The descriptor EvolutionEngine still selects package-owned trusted functions in worker subprocesses; those workers are fault containment only and are not a security boundary. The separate CodeEvolutionEngine may evaluate source proposals only after static AST review and a successful rootless sandbox preflight. Candidate and input files are mounted read-only; the container has no network, runs as non-root, drops capabilities, sets no-new-privileges, and applies cgroup/process/file/output/time limits. A trusted wrapper supplies fresh inputs, checks correctness, and measures algorithm-only samples inside the container. The host independently validates the wrapper output. The target Windows/WSL host must pass sandbox-check and sandbox-smoke before operators invoke code-evolve.

## Execution backends

The backend protocol separates preparation from repeated algorithm execution. Python delegates to the existing isolated worker. Rust and C compile fixed, repository-owned source files; their build flags, source SHA-256, binary SHA-256, compiler version, and compilation duration are recorded separately. Native backends currently expose internal algorithm elapsed time but no comparable peak-memory metric. The CLI backend-check command verifies each backend against the same independent Python sorting oracle and records dataset/environment provenance. It intentionally does not rank native and Python runtimes as if their measurement scopes were identical. The source-level code engine ranks Python candidates using the trusted wrapper's internal perf_counter_ns samples; container startup wall time is recorded separately and is not used for ranking.


## Static project analysis

The \`metaforge project analyze <root>\` command captures the bounded allow-listed project surface and emits a deterministic inventory. It classifies supported source files by language and parses Python with the standard-library AST to extract import edges and public top-level class/function signatures. Syntax errors become diagnostics; project modules are never imported or executed. Other languages are currently inventoried by file type only and require dedicated parsers for dependency and symbol graphs. Every analyzed file is re-read through the secure snapshot reader and checked against its captured SHA-256 before parsing. The JSON report records the snapshot fingerprint and explicit analysis scope.


### Static dependency graph

The static project report also includes a Python dependency graph derived only from import statements resolved to modules present in the captured snapshot. It reports directed edges, strongly connected components (cycles), isolated modules, and graph metrics. Cycle detection is iterative to avoid recursion-depth failure on long dependency chains. Entry points are limited to explicit `__main__.py` modules and syntactic `if __name__ == "__main__"` guards. These are structural indicators, not proof that a package is installable or that an entry point executes successfully. Imports that cannot be resolved within the captured Python module inventory remain external-or-unresolved and are excluded from graph edges. No project code, build backend, plugin, or dependency manifest is executed or loaded.


The dependency graph includes per-module in-degree and out-degree, explicit entry-point nodes, and a conservative reachability traversal over resolved local import edges. Reachability means structurally reachable from a detected entry point; it does not establish runtime execution or dynamic-import behavior.


### Dependency manifest inventory

The static project analyzer reads a small, explicit allow-list of root dependency manifests
(pyproject.toml, requirements.txt, package.json, Cargo.toml, go.mod, composer.json,
and pom.xml) through the same bounded, hash-verified snapshot path as source files.
It records declared dependency names, version requirements, and manifest groups.
It does not install packages, execute build scripts, contact registries, or claim that
a declaration is a resolved dependency graph. Manifest files remain protected from
project patch operations.
