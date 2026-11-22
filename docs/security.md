# Security model

## Current controls

- Candidate descriptors are declarative and validated against fixed strategy and pivot allow-lists.
- The Candidate Safety Reviewer is a mandatory gate in the evaluation path.
- The worker dispatches only to trusted package-owned implementations.
- Input types and item counts are validated.
- Worker execution has a timeout; the experiment scheduler also applies global wall-clock, evaluation-count, and concurrency budgets.
- Correctness is checked against an independent oracle before performance ranking.
- Agent results are treated as data and cannot authorize execution or bypass gates.

## Explicit limitations

The worker process provides fault containment, not a security boundary. It inherits the host user's filesystem and network permissions. The output-size threshold is checked after subprocess output has been captured, so it is not a hard memory limit. Python tracemalloc measures traced Python allocations, not total process memory. The current implementation has no OS-enforced CPU, memory, process-count, or filesystem quota.

Do not execute hostile or untrusted source code through this runner. Do not provide credentials, home directories, SSH keys, cloud tokens, host sockets, or sensitive files to candidate processes.

## Requirements before arbitrary code execution

A future sandbox must fail closed and be independently tested. At minimum:

- dedicated unprivileged identity with no ambient credentials;
- isolated, minimal, preferably read-only filesystem;
- network disabled by default;
- OS-enforced CPU, memory, process-count, file-size, and wall-clock quotas;
- bounded stdout/stderr capture at the OS or supervisor boundary;
- syscall restrictions or equivalent platform-specific confinement;
- process-tree termination and cleanup on timeout or cancellation;
- no host Docker socket, package-manager credentials, or arbitrary mounts;
- audit logs and reproducible sandbox image/version.

The Rust and C backends compile only fixed source files shipped in this repository. They do not accept candidate-provided source paths or generated code. Until the sandbox controls above exist, agent-generated source remains data for review and is never compiled or executed by MetaForge.

## V0.6 container sandbox status

The sandbox runner requires a rootless Podman or rootless Docker runtime and an image reference pinned by SHA-256 digest. It does not pull images. The preflight checks rootless mode and local image availability. The run policy disables networking, uses a non-root UID, read-only root filesystem, a read-only bind mount containing only candidate.py and input.json, dropped capabilities, no-new-privileges, CPU/memory/PID limits, a no-exec tmpfs, wall-clock timeout, and bounded output streams.

The runner is integrated with the separate V0.7 CodeEvolutionEngine, which performs static AST review and then executes source only through the fail-closed rootless container runner. The descriptor-only EvolutionEngine remains unchanged. A live rootless Podman smoke passed in GitHub Actions with the pinned image; the target Windows/WSL host still requires its own preflight and smoke before operators run code evolution. Container escape review and dedicated-host/VM isolation remain required for high-risk use.
