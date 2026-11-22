# Experiment protocol

1. Define the problem, input contract, independent correctness oracle, and candidate interface.
2. Fix the seed, dataset, population, generation count, repeat count, evaluation budget, concurrency, and wall-clock deadline.
3. Record the dataset SHA-256, source revision, Python version, OS, and hardware metadata.
4. Review every candidate descriptor before execution.
5. Reject incorrect candidates before comparing performance.
6. Run candidates under equivalent resource limits and repeat measurements.
7. Report median and distributional metrics; do not overinterpret one timing sample.
8. Preserve immutable JSON archives, dataset provenance, candidate fingerprints, and parent lineage.
9. Replay archived CLI experiments to verify the candidate sequence and correctness outcomes.
10. Treat timing replay as a new measurement, not a bit-for-bit performance reproduction.
11. Test multiple input sizes and distributions before making general claims.
12. Keep compilation time and runtime separate for future compiled-language backends.

## Interpretation

Local microbenchmarks are sensitive to CPU frequency, thermal state, background load, interpreter version, allocator behavior, and scheduler decisions. A faster candidate in one run is not evidence of a generally superior algorithm. Use paired repeated trials, representative datasets, and independent environments before promoting an optimization.
