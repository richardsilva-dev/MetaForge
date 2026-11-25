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


### External dependency consistency

Python import roots are classified as local, standard-library, external-or-unresolved,
or unresolved-relative. External import roots are compared with declared Python
distribution names using normalized exact-name matching only. A match is evidence of
a matching declaration, not proof of installation or runtime availability. A missing
match is not proof of an undeclared dependency because distribution names and import
names can differ, namespace packages can span distributions, and dynamic imports are
not fully visible to static AST analysis. No package resolution or installation occurs.


### Python public API contracts

For Python modules, the analyzer records a conservative module contract: literal
string-only __all__ declarations when statically available, inferred public top-level
class/function definitions otherwise, and signatures for public methods of exported
classes. Dynamic __all__ expressions produce a warning and fall back to visible public
definitions. This is a static source contract, not a guarantee of runtime exports:
module-level mutation, decorators, metaprogramming, conditional definitions, and
dynamic imports can alter the effective API.


### Declared application entry points

The analyzer inventories script and binary declarations from supported package manifests.
Python console scripts, GUI scripts, plugin entry points, and Poetry scripts are checked
against the statically analyzed module and symbol inventory. Rust Cargo binaries,
Node package bins, and Composer binaries are recorded but not resolved by language
parsers yet. No declared command, build script, or application entry point is executed.


### Dynamic Python imports

The analyzer recognizes statically bounded literal targets passed to the built-in
__import__ and importlib.import_module forms, including common import aliases and
literal relative imports with a literal package argument. It adds these edges to the
static import graph. Non-literal, malformed, oversized, and unsupported targets produce
diagnostics rather than being evaluated. This does not resolve runtime-generated
module names or emulate Python import hooks.


## Contract-aware multi-file patch review

The project review workflow validates a structured multi-file patch against a captured
snapshot, applies it only to a disposable copy, and compares before/after static
architecture reports. By default it blocks removed or changed public API contracts,
regressed or unresolved Python entry points, and newly introduced dependency cycles.
It reports module additions/removals, dependency declaration changes, newly unresolved
imports, and newly isolated modules as review evidence. Explicit flags can permit
breaking API changes, new cycles, or unresolved entry points; these exceptions are
recorded in the report. Review does not execute project code, tests, build systems, or
package managers, and the candidate copy is discarded after the report is produced.

The CLI command is:

    python -m metaforge.cli project review <project-root> <patch.json>

The review report is a static regression gate, not a substitute for independent tests,
security scanning, runtime validation, or performance measurement.


To create a separate candidate copy after the default contract review accepts a patch:

    python -m metaforge.cli project apply <project-root> <patch.json> --destination <candidate-directory>

The destination parent directory must already exist, and the destination itself must not. A rejected patch creates no candidate copy.
The original project is never modified by this command. The candidate is not executed
or tested; it is a reviewable artifact for subsequent validation.


## Static validation planning

The validation planner inventories likely test targets using language-specific path
conventions and declared test-framework dependencies. It also runs the existing
redacted secret scanner and summarizes architecture risks. The planner does not
execute tests, builds, project modules, package managers, or network operations.
Framework identification is heuristic: a discovered test file does not prove that
its runner or all test dependencies are installed.

Command:

    python -m metaforge.cli project validation-plan <project-root>

The report separates completed static checks from test execution, runtime security,
and performance validation, which remain explicitly not executed until a compatible
isolated toolchain profile is selected.
