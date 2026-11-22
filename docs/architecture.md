# Architecture

The candidate model is declarative. A seeded generator creates bounded candidates; a worker process dispatches only to trusted allow-listed implementations. The evolution engine compares each output with an independent sorting oracle, rejects incorrect candidates, measures repeated execution, and selects valid candidates for the next generation.

The current implementation is intentionally narrow: integer sorting in Python. Future language backends must implement a versioned interface and use the same correctness and benchmark contract. Compilation time and runtime must be measured separately.
