# MetaForge

**A safety-first laboratory for evolutionary algorithm research and meta-engineering.**

MetaForge currently provides two related experimental paths:

- A deterministic, descriptor-based optimizer for integer sorting, including trusted Python, C11, and Rust implementations.
- A separate source-level code-evolution pipeline that accepts AI-proposed Python implementations of a strict `solve(values)` contract, reviews them, executes them only in a verified rootless container, checks correctness against an independent oracle, and records lineage and measurements.

> **Current scope:** source-level code evolution is currently limited to pure Python integer-sorting candidates. The C and Rust backends execute repository-owned implementations; they are not yet general-purpose compilers for AI-generated programs. Project snapshots and structured patches are now available as a host-side foundation, but are not yet connected to agents, project builds, test execution, or automatic refactoring. MetaForge is not yet a repository-wide architecture-rewriting agent.

## Current capabilities

- Deterministic specialist-agent contracts, registry, and workflow.
- Candidate Designer, mandatory Candidate Safety Reviewer, and Benchmark Analyst.
- Optional LangGraph adapter (the orchestration extra); the core does not require an LLM or orchestration framework.
- Built-in sort, insertion sort, merge sort, heapsort, shellsort, quicksort, and hybrid quicksort variants.
- Seeded population generation, mutation, crossover, Pareto/crowding selection, strategy diversity preservation, and candidate lineage.
- Multi-objective ranking across median runtime and traced Python allocation peak, with raw repeat samples and median absolute deviation.
- Source-level evolution promotion is gated against a trusted `sorted()` baseline measured in the same rootless sandbox and workload suite; candidates must beat it by at least 5% on the equal-weight aggregate score, keep each workload's regression within 2%, clear a robust median-absolute-deviation (MAD) spread check, and provide at least five samples per workload. This is a conservative engineering filter, not a formal significance test; independent reproduction remains required. Correct but slower or inconclusive candidates remain archived but are not promoted.
- Correctness gate against Python's independent `sorted()` oracle.
- Repeated timing and traced Python-memory measurements.
- Bounded parallelism, evaluation budget, worker timeout, and global experiment deadline.
- Dataset SHA-256, run identifier, UTC timestamps, environment metadata, configuration, and immutable JSON archives.
- Deterministic candidate-sequence replay from archived synthetic CLI datasets.
- A source-level Python code-evolution engine with structured model proposals, static AST review, rootless-container execution, an independent correctness oracle, mutation lineage, and replay without model calls.
- A bounded project-snapshot and structured PatchSet foundation: SHA-256 manifests, strict relative-path policy, optimistic preconditions, immutable existing acceptance tests, and atomic patch application into a new disposable copy.
- A multilingual text-source patch contract for Python, Rust, C/C++, Go, Java/Kotlin, C#, Swift, Scala, JavaScript/TypeScript, and additional source formats; this is not yet a model-driven project-refactoring runtime.
- Versioned backend interfaces and trusted C11/Rust implementations for selected sorting strategies.

## Architecture

    Problem + dataset
          |
    Candidate Designer / bounded source-proposal agent
          |
    Candidate descriptor or source proposal
          |
    Static safety policy (source proposals)
          |
    Bounded evaluation scheduler
          |
    Trusted worker or verified rootless container
          |
    Independent correctness oracle
          |
    Repeated benchmark -> Analyst -> selection -> mutation
          |
    Lineage + environment + dataset hash
          |
    Immutable JSON archive + Markdown report

Agent output is untrusted input. Agents can propose or summarize; deterministic code decides whether a candidate passes safety and correctness gates. The source-level pipeline requires both static review and a successful sandbox preflight/smoke test before model calls or candidate execution. Static analysis is defense in depth, not a security boundary.

Project patch contracts are separate from the current code-evolution engine. A snapshot includes only allow-listed UTF-8 text files under `src/`, `tests/`, `docs/`, and `backends/`, plus selected root documentation files. Hidden directories, environments, caches, build outputs, binary assets, and dependency/build manifests are not included. Shell/batch scripts and build manifests are protected. Existing files under `tests/` cannot be replaced or deleted; new tests can be added. Applying a PatchSet verifies the source snapshot and expected file hashes, then creates a new directory atomically without modifying the original checkout. No project code is executed by this API. A local heuristic scanner can flag common credential formats in captured text files, but it is not a complete secret detector and must not be treated as proof that files are safe to transmit.


## Project snapshots and PatchSets (foundation API)

This is the first safe file-editing layer for future refactoring workflows. It is intentionally not exposed as an LLM command yet.

\`\`\`python
from metaforge.projects import (
    ProjectPatch,
    apply_patch_to_copy,
    capture_project_snapshot,
    scan_project_secrets,
)

snapshot = capture_project_snapshot("path/to/project")
secret_report = scan_project_secrets(snapshot)
if not secret_report.no_detected_secrets:
    raise RuntimeError("Secret scan incomplete or findings detected; do not use project contents")

original = snapshot.file_map["src/module.py"]

patch = ProjectPatch.from_dict({
    "schema_version": "1.0",
    "rationale": "Refactor one bounded function without changing its contract",
    "changes": [{
        "path": "src/module.py",
        "action": "replace",
        "expected_sha256": original.sha256,
        "content": "def solve():\n    return 42\n",
    }],
})

patched_snapshot = apply_patch_to_copy(
    snapshot,
    patch,
    "path/to/new-disposable-project-copy",
)
print(patched_snapshot.fingerprint)
\`\`\`

The destination must not exist and must not overlap the source project. Patch operations support \`create\`, \`replace\`, and \`delete\`; replacements and deletions require the exact SHA-256 from the captured snapshot. Paths are normalized relative POSIX paths and restricted to source/documentation roots and allow-listed text extensions. The default limits are 30 files per patch, 256 KiB per file, 1 MiB total patch content, 1,000 snapshot files, and 20 MiB snapshot content.

**Not implemented yet:** sending project content to an agent, complete secret scanning/redaction for model context, immutable project-test execution, no-network build orchestration, multi-language compiler execution of generated patches, dependency-graph analysis, and architecture-level acceptance. The local secret scanner is heuristic, may miss credentials or report false positives, and is not a guarantee that files are safe to transmit. The project patch API only produces and verifies a disposable text-source copy; binary assets are excluded, and it does not claim that the copy builds or passes tests.

## Requirements and installation

- Python 3.11+
- pytest for development

    python -m venv .venv
    # Windows PowerShell: .venv\Scripts\Activate.ps1
    # Linux/macOS: source .venv/bin/activate
    python -m pip install -e ".[dev]"

Optional LangGraph integration:

    python -m pip install -e ".[dev,orchestration]"

Optional OpenAI Agents SDK candidate proposals:

    python -m pip install -e ".[dev,llm]"

For the deterministic descriptor workflow, optional model proposals can be enabled with `--llm-proposals`. They must match the registered candidate descriptor schema and are never executed as generated source.

The separate source-level workflow uses the optional OpenAI Agents SDK to propose inert Python source for the restricted integer-sorting contract. It has no model tools or handoffs, uses a strict structured output, and is bounded by per-call and global time/call budgets. This mode requires an API key and can incur API charges. Do not put credentials in source files, experiment artifacts, shell history, or GitHub.

## Run a deterministic experiment

    python -m metaforge.cli run --size 200 --generations 3 --seed 7 --repeats 3 --population-size 15 --max-evaluations 64 --max-workers 2 --global-timeout 120

## Run source-level code evolution

Configure rootless Podman or Docker and a local image pinned by SHA-256 digest, then pass the preflight and isolation smoke commands before enabling model calls:

    python -m metaforge.cli sandbox-check
    python -m metaforge.cli sandbox-smoke

With the optional Agents SDK installed and API key/model set in the shell:

    python -m metaforge.cli code-evolve --size 5000 --population-size 2 --generations 2 --repeats 3 --max-agent-calls 4

The source proposal contract currently requires a pure `solve(values)` function returning an ascending list of signed 64-bit integers. Each candidate is evaluated on eight deterministic workload profiles derived from the same integrity-checked dataset: original, ascending, descending, nearly sorted, duplicate-heavy, low-cardinality, organ-pipe, and all-equal. Each profile receives the same repeat count; the selection score is the median of per-workload medians, so a candidate cannot win by exploiting only one input ordering. Proposed code is reviewed, executed in a rootless container with no network, and checked against the independent host-side sorting oracle on every workload. Model output is never trusted as proof of safety or correctness.

The CLI writes:
- `.artifacts/code-evolution.json` and `.artifacts/code-evolution.md` as convenient latest-run pointers.
- `.artifacts/runs/<run_id>.json` and `.artifacts/runs/<run_id>.md` as immutable run archives.

The CLI's synthetic dataset is embedded in its run artifact so the experiment can be replayed. Do not put confidential or personal data in experiment artifacts.

## Replay a source-evolution run

    python -m metaforge.cli code-replay --run .artifacts/runs/<run_id>.json

Replay verifies the embedded dataset hash, benchmark-suite version and workload fingerprints, and candidate source fingerprints; it repeats static review, rechecks the rootless sandbox, and measures archived candidate source without model calls. Older single-input archives remain replayable. Hardware, runtime, image, and load may differ; timing values are not expected to match exactly.

## Compiled-language backends

MetaForge defines a versioned execution-backend contract and includes repository-owned C11 and Rust implementations for built-in sort, heapsort, and shellsort. Rust compilation is dependency-free and uses Cargo offline. Both native backends report compilation time separately from algorithm runtime.

Install a Rust toolchain (rustc and cargo) and a C compiler (cc, clang, or gcc), then run:

    python -m metaforge.cli backend-check --backend both --strategy heapsort --size 1000 --repeats 3

The command checks outputs against Python's `sorted()` oracle and writes a JSON report. Python peak memory uses tracemalloc; native backends currently do not report comparable peak-memory measurements. Runtime measurement scopes differ, so the report deliberately does not claim cross-language performance rankings. Only trusted source files already in this repository are compiled; generated native source is not accepted.

## Security boundary and threat model

The source-level execution path is intended to limit damage from untrusted generated Python code. It requires a rootless container, immutable local image digest, disabled networking, read-only root filesystem and workspace, non-root UID, dropped Linux capabilities, no-new-privileges, bounded CPU/memory/PIDs/swap, bounded temporary storage and output, process/file limits, a wall-clock timeout, and explicit in-container policy checks.

The AST reviewer is a conservative filter, not a proof of safety. In particular, Python reflection and interpreter internals are too dynamic to make AST review a sufficient isolation boundary. A candidate is accepted only after both review and sandbox evaluation. Runtime/kernel vulnerabilities, container-runtime vulnerabilities, side channels, and resource exhaustion against the host/daemon cannot be ruled out. Use a disposable VM or dedicated isolated host for high-risk research. Never run the system against production credentials or sensitive data.

See [SECURITY.md](SECURITY.md) for the threat model, controls, known limitations, and reporting process.

## Tests and validation

    python -m pytest

CI covers Python 3.11, 3.12, and 3.13; optional LangGraph/OpenAI Agents SDK adapters; Hypothesis property tests; C/Rust formatting, lint, static analysis and differential backend checks; CodeQL for Python/C++; and a live rootless Podman job with isolation and end-to-end tests. The target Windows/WSL host must pass its own preflight and smoke validation.

Benchmark values are exploratory. Do not claim general performance improvements without controlled repeated comparisons across representative workloads, multiple runs, and equivalent environments.

## Roadmap

The planned language-neutral and project-refactoring architecture is specified in [the multi-language refactoring design](docs/architecture/multilanguage-project-refactoring.md). This is a design target, not a claim that repository-wide refactoring is already available.

- **V0.1–V0.6:** controlled optimizer, agent contracts, provenance/replay, compiled trusted backends, and rootless sandbox.
- **V0.7:** source-level Python code generation, static review, sandboxed correctness/performance evaluation, lineage, and replay.
- **V0.8:** adversarial AST-policy hardening, expanded security regressions, and multi-workload benchmark selection.
- **V0.9:** language-neutral candidate contracts and compiler-backed generated-source evaluation for explicitly supported toolchains.
- **V0.10 foundation:** bounded project snapshots and hash-preconditioned PatchSets applied only to new disposable copies. Agent integration and isolated project build/test orchestration are still pending.
- **V1.0:** multi-file refactoring and architecture optimization with isolated worktrees, project-specific test plans, dependency/build controls, secret scanning, and evidence-based acceptance gates.
