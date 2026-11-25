# Sandbox operator guide

## Status

The fail-closed Python container runner is implemented and used by the V0.7 CodeEvolutionEngine after static AST review. It is not used by the descriptor-only EvolutionEngine. The complete smoke passed in GitHub Actions using rootless Podman 5.7.0 and the pinned Python 3.12 image. The operator's target host still requires its own preflight and smoke validation before invoking code evolution.

## Required configuration

- Rootless Podman or rootless Docker.
- A locally available Python image referenced by immutable SHA-256 digest. The currently documented reference is `python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df`, listed as the official multi-platform Python 3.12 slim Bookworm image on Docker Hub. Verify the digest and platform before use.
- The image must contain Python 3 and the standard library.
- The image must not require network access at runtime.
- The runtime must support memory, CPU, PID, read-only rootfs, tmpfs, and user restrictions.

Set:

- `METAFORGE_SANDBOX_RUNTIME=podman` (or `docker` only when its security options report rootless mode).
- `METAFORGE_SANDBOX_IMAGE=repository/name@sha256:<64 lowercase hex characters>`.

Run `python -m metaforge.cli sandbox-check` for preflight only. Then run `python -m metaforge.cli sandbox-smoke` to execute a fixed trusted probe that checks non-root UID, no network, read-only root/workspace mounts, no-exec tmpfs, dropped capabilities, no-new-privileges, cgroup CPU/memory/PID limits, process/file limits, and absence of OPENAI_API_KEY inside the container. Neither command pulls images.

## Enforced container configuration

The runner uses a non-root UID/GID, no network, read-only root filesystem, one read-only bind mount containing `candidate.py`, `input.json`, and the trusted timing wrapper, dropped Linux capabilities, no-new-privileges, CPU and memory limits with swap disabled, a PID limit, bounded file descriptors and file size, a small no-exec tmpfs, bounded stdout/stderr readers, wall-clock timeout, and container kill/removal on timeout or output overflow. Every run receives a unique container name before launch; timeout cleanup falls back to that name and retries when the runtime has not written its container-ID file yet.

The container receives no explicit host environment variables. The host runtime client receives only the minimum connection-related environment needed to reach the rootless runtime. The trusted wrapper reads `/workspace/input.json`, loads the candidate module, and calls its pure `solve(values)` function repeatedly with fresh copies. The wrapper measures algorithm time with `perf_counter_ns`, validates each result, and emits structured JSON. The host independently verifies the final output. Container wall time and algorithm-only samples are recorded separately.

## Threat model and residual risks

This is defense in depth, not a proof against container escapes. The host kernel, runtime, image, and rootless user-namespace configuration remain part of the trusted computing base. A malicious candidate may still exploit a kernel/runtime vulnerability or attack the runtime daemon. For high-risk generated code, run MetaForge inside a disposable dedicated VM with no personal files, credentials, SSH agent, cloud metadata, or sensitive network access.

The image digest must be reviewed and updated deliberately. The runner uses pull=never, so it cannot silently fetch a new image. Do not replace the digest with a mutable tag.

## Operator gate

The source-level CodeEvolutionEngine is implemented and the complete scripted code-generation-to-container path passes in CI. Before invoking `code-evolve` on a target machine, the operator must:
1. install and start a rootless runtime;
2. pull the exact pinned image digest;
3. run `sandbox-check` and confirm `rootless: true`;
4. run `sandbox-smoke` and confirm every isolation probe passes;
5. keep the model API key in the local host environment only.

If any check fails, do not invoke `code-evolve`. The CLI performs preflight again before making model calls and stops safely if it fails.
