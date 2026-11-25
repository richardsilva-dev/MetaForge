# Security model

## Execution paths

MetaForge has two separate evaluation paths.

### Descriptor EvolutionEngine

- Candidates are declarative descriptors validated against fixed strategy and parameter allow-lists.
- The mandatory Candidate Safety Reviewer gates every descriptor.
- Workers dispatch only to package-owned trusted implementations.
- The worker subprocess is fault containment, not a security boundary.
- Input types/counts, timeouts, global wall-clock, evaluation-count, and concurrency budgets are enforced.
- An independent correctness oracle gates performance ranking.

### Source-level CodeEvolutionEngine

- The optional OpenAI Agents SDK code designer has no tools or handoffs, returns bounded Pydantic output, and is limited to one turn per proposal.
- Model output is untrusted. A static AST reviewer rejects disallowed imports, file access, stdout calls, dynamic execution, unsafe module-level statements, decorators, defaults, and annotations. This is defense in depth, not a security boundary.
- The engine requires successful rootless runtime and local pinned-image preflight before it makes any model call.
- Reviewed source is executed only in a rootless Podman/Docker container. It is never executed on the host.
- Candidate source, input JSON, a trusted supervisor, and a separate candidate worker are mounted read-only. The supervisor never imports candidate code; each sample runs in a fresh worker interpreter.
- The container has networking disabled, a non-root UID, read-only rootfs, dropped capabilities, no-new-privileges, CPU/memory/PID limits, swap disabled, bounded tmpfs/shared memory, file/descriptor/core limits, output limits, and a wall-clock timeout.
- The worker installs a Linux seccomp filter before loading candidate source. It denies process-signal syscalls, ptrace, pidfd signal/descriptor access, and cross-process memory syscalls; unsupported architectures or filter-installation failures stop execution.
- The supervisor measures each worker's end-to-end process lifetime with its own monotonic clock and independently checks the worker result against the expected output. Samples include interpreter startup and IPC overhead; they are not pure algorithm-only timings. Container startup wall time is reported separately.
- The host independently validates the supervisor's JSON output and timing sample schema.
- The host API key and other host environment variables are not passed into the container. The model API key is used only by the host-side SDK client.

## Explicit limitations

- A container is not a proof against kernel/runtime vulnerabilities or container escapes. Use a dedicated isolated host or VM for high-risk research.
- The static AST reviewer is not a sandbox and must never be treated as one.
- Project snapshots cap both included file count/bytes and the total number of directory entries traversed, including non-text and ignored entries, to bound traversal work and memory.
- The worker seccomp filter is a targeted denylist, not a complete syscall allow-list or proof against kernel/runtime vulnerabilities. The supervisor and worker still share a container UID; CPU, memory, PID, and wall-clock limits remain necessary to bound denial-of-service attempts.
- Python tracemalloc does not measure total process memory. The source-level sandbox enforces a cgroup memory ceiling but does not yet report reliable per-candidate peak memory.
- Source-level performance is measured only for Python candidates in the same pinned runtime/image. Do not compare those samples directly with C/Rust backend timings.
- The target Windows/WSL host has not yet been validated; operators must run `sandbox-check` and `sandbox-smoke` there before invoking `code-evolve`.

## Credential and data handling

Do not commit API keys or place them in experiment datasets. Configure `OPENAI_API_KEY` only in the local host environment. Candidate containers receive no host environment variables, host sockets, SSH keys, cloud tokens, home directories, or arbitrary mounts. The code-evolution CLI uses synthetic integer datasets by default and archives generated source for audit/replay.

## Native backends

The Rust and C backends compile only fixed source files shipped in this repository. They do not accept candidate-provided source paths or generated code. Generated C/Rust compilation is not enabled.

## V0.6 sandbox status

The runner requires rootless Podman or rootless Docker and an image reference pinned by SHA-256 digest. It does not pull images. The preflight checks rootless mode and local image availability. The trusted `sandbox-smoke` probe passed in GitHub Actions with rootless Podman 5.7.0 and the pinned official Python 3.12 slim Bookworm image. The target operator machine still needs its own preflight and smoke run.
