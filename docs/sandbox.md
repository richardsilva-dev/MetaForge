# Sandbox operator guide

## Status

The fail-closed Python container runner is implemented as an isolated subsystem. It is deliberately not wired into the evolutionary engine and no agent-generated source is executed by default. CI tests use a fake runtime to verify policy construction, rootless preflight behavior, timeout handling, output limits, and cleanup. A real container engine and pinned image still require validation on the operator's host.

## Required configuration

- Rootless Podman or rootless Docker.
- A locally available Python image referenced by immutable SHA-256 digest.
- The image must contain Python 3 and the standard library.
- The image must not require network access at runtime.
- The runtime must support memory, CPU, PID, read-only rootfs, tmpfs, and user restrictions.

Set:

- `METAFORGE_SANDBOX_RUNTIME=podman` (or `docker` only when its security options report rootless mode).
- `METAFORGE_SANDBOX_IMAGE=repository/name@sha256:<64 lowercase hex characters>`.

Run `python -m metaforge.cli sandbox-check`. This performs preflight only; it does not execute source code and does not pull images.

## Enforced container configuration

The runner uses a non-root UID/GID, no network, read-only root filesystem, one read-only bind mount containing only `candidate.py`, dropped Linux capabilities, no-new-privileges, CPU and memory limits with swap disabled, a PID limit, bounded file descriptors and file size, a small no-exec tmpfs, bounded stdout/stderr readers, wall-clock timeout, and container kill/removal on timeout or output overflow.

The container receives no explicit host environment variables. The host runtime client receives only the minimum connection-related environment needed to reach the rootless runtime. The candidate receives its integer input over stdin as JSON and must return a JSON list of integers on stdout. An independent host-side oracle verifies the result.

## Threat model and residual risks

This is defense in depth, not a proof against container escapes. The host kernel, runtime, image, and rootless user-namespace configuration remain part of the trusted computing base. A malicious candidate may still exploit a kernel/runtime vulnerability or attack the runtime daemon. For high-risk generated code, run MetaForge inside a disposable dedicated VM with no personal files, credentials, SSH agent, cloud metadata, or sensitive network access.

The image digest must be reviewed and updated deliberately. The runner uses pull=never, so it cannot silently fetch a new image. Do not replace the digest with a mutable tag.

## Integration gate

Do not connect this runner to the evolution engine until:
1. a real rootless runtime passes preflight on the target host;
2. a pinned minimal Python image is verified locally;
3. an end-to-end container test confirms all restrictions and teardown behavior;
4. output/timeout tests are repeated against the real runtime;
5. the sandbox threat model is reviewed independently.

Until then, agent-generated code remains inert text and trusted allow-listed algorithms remain the only executable candidates.
