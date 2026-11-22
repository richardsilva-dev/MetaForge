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

The registry is explicit and does not dynamically import arbitrary agent classes. The built-in specialists are deterministic reference implementations. The optional LangGraph adapter orchestrates registered roles without changing their permissions or bypassing deterministic gates. The optional OpenAI Agents SDK adapter can propose the initial population through strict Pydantic output, with no tools or handoffs, one turn, a per-call timeout, and trace payloads disabled. Proposals are accepted only if they exactly match the registered candidate search space; deterministic fallback fills missing proposals. A future LLM adapter must use bounded structured inputs/outputs, validate all responses, apply time/token budgets, and treat all model output as untrusted.

## Candidate identity and lineage

A candidate fingerprint identifies its declarative algorithm parameters. It is distinct from lineage: lineage records the generation, operator, origin, and parent fingerprint for each population instance. This allows identical candidate configurations to be recognized while preserving the history of how a candidate entered a generation.

## Reproducibility

Each run records a unique ID, UTC timestamps, seed, SHA-256 of the canonical dataset encoding, Python/runtime/platform metadata, source revision when supplied by the environment, and experiment configuration. CLI runs preserve their synthetic input dataset in the archive. Every generation stores its exact candidate population and lineage. Replay consumes that stored schedule rather than re-running timing-dependent selection, so the candidate sequence is reproducible. Exact timing reproduction is not promised.

## Execution boundary

The worker subprocess is fault containment only. It inherits host filesystem and network permissions. Candidate descriptors select package-owned trusted functions; they are not source code. Arbitrary agent-generated code remains disabled until a separately reviewed OS-level sandbox is available.

## Execution backends

The backend protocol separates preparation from repeated algorithm execution. Python delegates to the existing isolated worker. Rust and C compile fixed, repository-owned source files; their build flags, source SHA-256, binary SHA-256, compiler version, and compilation duration are recorded separately. Native backends currently expose internal algorithm elapsed time but no comparable peak-memory metric. The CLI backend-check command verifies each backend against the same independent Python sorting oracle and records dataset/environment provenance. It intentionally does not rank native and Python runtimes as if their measurement scopes were identical.
