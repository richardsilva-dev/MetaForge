# Experiment protocol

1. Record problem definition, candidate parameters, seed, dataset size, Python version, OS, and hardware.
2. Keep the correctness oracle independent of candidate implementations.
3. Reject incorrect outputs before comparing performance.
4. Repeat candidates under identical conditions and report distributions.
5. Test multiple input distributions and sizes before generalizing.
6. Preserve raw JSON output and source revision for replay.
7. Treat local timing as exploratory unless execution conditions are controlled.
