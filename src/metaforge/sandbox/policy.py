from __future__ import annotations

from dataclasses import dataclass
import re


_IMAGE_DIGEST = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@-]*@sha256:[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    image: str
    runtime: str = "podman"
    timeout_seconds: float = 5.0
    memory_mb: int = 256
    cpu_limit: float = 1.0
    pids_limit: int = 64
    tmpfs_mb: int = 16
    max_source_bytes: int = 256_000
    max_input_items: int = 100_000
    max_output_bytes: int = 1_000_000

    def __post_init__(self) -> None:
        if not isinstance(self.image, str) or not _IMAGE_DIGEST.fullmatch(self.image):
            raise ValueError("Sandbox image must be pinned to an immutable sha256 digest")
        if not isinstance(self.runtime, str) or self.runtime not in {"podman", "docker"}:
            raise ValueError("Sandbox runtime must be podman or docker")
        if type(self.timeout_seconds) not in (int, float) or not 0.1 <= self.timeout_seconds <= 120:
            raise ValueError("Sandbox timeout must be 0.1..120 seconds")
        if type(self.memory_mb) is not int or not 64 <= self.memory_mb <= 2048:
            raise ValueError("Sandbox memory limit must be 64..2048 MiB")
        if type(self.cpu_limit) not in (int, float) or not 0.1 <= self.cpu_limit <= 4:
            raise ValueError("Sandbox CPU limit must be 0.1..4")
        if type(self.pids_limit) is not int or not 16 <= self.pids_limit <= 256:
            raise ValueError("Sandbox pids limit must be 16..256")
        if type(self.tmpfs_mb) is not int or not 1 <= self.tmpfs_mb <= 128:
            raise ValueError("Sandbox tmpfs limit must be 1..128 MiB")
        if type(self.max_source_bytes) is not int or not 1024 <= self.max_source_bytes <= 256_000:
            raise ValueError("Source limit must be 1024..256000 bytes")
        if type(self.max_input_items) is not int or not 0 <= self.max_input_items <= 100_000:
            raise ValueError("Input item limit must be 0..100000")
        if type(self.max_output_bytes) is not int or not 1024 <= self.max_output_bytes <= 1_000_000:
            raise ValueError("Output limit must be 1024..1000000 bytes")
