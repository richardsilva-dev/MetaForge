# Experiment protocol

1. Define the problem, input contract, independent correctness oracle, and candidate interface.
2. Fix the seed, dataset, population, generation count, repeat count, evaluation budget, concurrency, and wall-clock deadline.
3. Record the dataset SHA-256, source revision, Python version, OS, and hardware metadata.
4. Review every candidate descriptor before execution.
5. Reject incorrect candidates before comparing performance.
6. Run candidates under equivalent resource limits and repeat measurements.
7. Preserve every repeat sample, report the median and median absolute deviation, and do not overinterpret one timing sample. Track runtime and traced Python allocation peak as separate objectives.
8. Preserve immutable JSON archives, dataset provenance, candidate fingerprints, and parent lineage.
9. Replay archived CLI experiments to verify the candidate sequence and correctness outcomes.
10. Treat timing replay as a new measurement, not a bit-for-bit performance reproduction.
11. Test multiple input sizes and distributions before making general claims.
12. Keep compilation time and runtime separate for future compiled-language backends.

## Interpretation

Local microbenchmarks are sensitive to CPU frequency, thermal state, background load, interpreter version, allocator behavior, and scheduler decisions. A faster candidate in one run is not evidence of a generally superior algorithm. Use paired repeated trials, representative datasets, and independent environments before promoting an optimization.

## Cross-language backend comparisons

Compile time must be reported separately from algorithm runtime. Record compiler/runtime version, exact build flags, source hash, binary hash, dataset hash, and measurement scope. The current Python backend reports worker-internal elapsed time with tracemalloc active; C and Rust report native algorithm-internal elapsed time without peak-memory measurement. These scopes are not directly comparable. First establish correctness parity and measurement equivalence before introducing cross-language selection or performance rankings.

## Source-level code evolution (V0.7)

1. Require a successful rootless sandbox preflight and trusted isolation smoke before any model call.
2. Ask the code-design agent for a bounded module defining only a pure `solve(values)` function.
3. Apply the AST policy gate. Rejected source is never loaded by the container wrapper.
4. Execute approved source only in the pinned rootless container. The trusted wrapper supplies a fresh input copy for every repeat, validates every result, and measures only the function call with `perf_counter_ns`.
5. Record algorithm samples separately from container startup wall time. Rank candidates by the median of algorithm samples; do not use container startup wall time as the algorithm score.
6. Preserve exact source bytes, SHA-256, parent fingerprint, generation, model label, static-review findings, dataset digest, and all timing samples.
7. Use `code-replay` to re-evaluate archived source without calling a model. Replay verifies the embedded dataset digest and each source fingerprint before review and execution.
8. Treat replay measurements as new observations. Differences in hardware, runtime, image, Python version, and system load prevent direct comparison with the original run.

The static AST policy is defense in depth, not a sandbox. The container remains the execution boundary. The source-level runner currently measures time but not per-candidate memory; do not claim a multiobjective memory/time optimum for generated Python source.
