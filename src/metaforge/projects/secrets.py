from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any

from metaforge.projects.models import ProjectContractError, ProjectPolicy, ProjectSnapshot
from metaforge.projects.snapshot import _read_relative_regular_file


@dataclass(frozen=True, slots=True)
class SecretFinding:
    """A redacted finding. Never contains the matched credential value."""

    path: str
    line: int
    rule_id: str
    severity: str = "high"

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path:
            raise ProjectContractError("Secret finding path must be non-empty text")
        if type(self.line) is not int or self.line < 1:
            raise ProjectContractError("Secret finding line must be a positive integer")
        if not isinstance(self.rule_id, str) or not self.rule_id:
            raise ProjectContractError("Secret finding rule_id must be non-empty text")
        if self.severity not in {"high", "medium"}:
            raise ProjectContractError("Secret finding severity must be high or medium")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "line": self.line,
            "rule_id": self.rule_id,
            "severity": self.severity,
        }


@dataclass(frozen=True, slots=True)
class SecretScanReport:
    """Redacted report suitable for policy decisions, not for exposing credentials."""

    snapshot_fingerprint: str
    files_scanned: int
    findings: tuple[SecretFinding, ...]
    findings_truncated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_fingerprint, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.snapshot_fingerprint
        ):
            raise ProjectContractError("Secret scan snapshot fingerprint must be SHA-256")
        if type(self.files_scanned) is not int or self.files_scanned < 0:
            raise ProjectContractError("Secret scan file count must be a non-negative integer")
        if not isinstance(self.findings, tuple) or any(
            not isinstance(item, SecretFinding) for item in self.findings
        ):
            raise ProjectContractError("Secret scan findings must be a tuple of SecretFinding records")
        if type(self.findings_truncated) is not bool:
            raise ProjectContractError("Secret scan truncated flag must be boolean")

    @property
    def no_detected_secrets(self) -> bool:
        return not self.findings and not self.findings_truncated

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "files_scanned": self.files_scanned,
            "finding_count": len(self.findings),
            "findings_truncated": self.findings_truncated,
            "no_detected_secrets": self.no_detected_secrets,
            "findings": [finding.to_dict() for finding in self.findings],
        }


@dataclass(frozen=True, slots=True)
class SecretScanPolicy:
    max_findings: int = 100

    def __post_init__(self) -> None:
        if type(self.max_findings) is not int or not 1 <= self.max_findings <= 10_000:
            raise ProjectContractError("max_findings must be an integer between 1 and 10000")


# Rules intentionally emit only a rule identifier and line number. Match
# objects and their secret-bearing groups are discarded immediately.
_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "private-key-header",
        re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"),
        "high",
    ),
    (
        "openai-style-api-key",
        re.compile(r"(?<![A-Za-z0-9])sk-(?:proj-)?[A-Za-z0-9_-]{24,}"),
        "high",
    ),
    (
        "github-token",
        re.compile(r"(?<![A-Za-z0-9])(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
        "high",
    ),
    (
        "aws-access-key-id",
        re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])"),
        "medium",
    ),
    (
        "google-api-key",
        re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{35}(?![A-Za-z0-9])"),
        "high",
    ),
    (
        "slack-token",
        re.compile(r"(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]{20,}"),
        "high",
    ),
    (
        "jwt-like-token",
        re.compile(
            r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\."
            r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"
        ),
        "medium",
    ),
    (
        "credential-assignment",
        re.compile(
            r"""(?ix)\b(?:api[_-]?key|client[_-]?secret|secret|secret[_-]?key|"""
            r"""access[_-]?token|auth[_-]?token|password|passwd|private[_-]?key)"""
            r"""\b\s*[:=]\s*["']?([^\s"']{8,})"""
        ),
        "medium",
    ),
    (
        "credential-url",
        re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@[^\s]+"),
        "high",
    ),
)

_PLACEHOLDER_VALUES = frozenset({
    "none", "null", "undefined", "changeme", "change_me", "change-this",
    "example", "placeholder", "your_api_key", "your-token", "your-secret",
    "redacted", "dummy", "test", "testing", "password", "secret",
    "os.getenv", "getenv(", "process.env", "env[", "settings.", "config.",
})


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().strip("\"'").strip().casefold()
    if normalized in _PLACEHOLDER_VALUES:
        return True
    return normalized.startswith((
        "your_", "your-", "example_", "example-", "placeholder_",
        "$" + "{", "$env:", "os.getenv", "getenv(", "process.env",
    ))


def scan_project_secrets(
    snapshot: ProjectSnapshot,
    policy: SecretScanPolicy | None = None,
    project_policy: ProjectPolicy | None = None,
) -> SecretScanReport:
    """Scan captured text files locally and fail closed on stale or unreadable input.

    Only path, line, rule ID, and severity are retained. Matched text is never
    included in the result or report. This is a conservative ruleset, not a
    proof that a source tree contains no secrets.
    """
    if not isinstance(snapshot, ProjectSnapshot):
        raise ProjectContractError("A ProjectSnapshot is required")
    policy = policy or SecretScanPolicy()
    project_policy = project_policy or ProjectPolicy()
    if not isinstance(policy, SecretScanPolicy):
        raise ProjectContractError("A SecretScanPolicy is required")
    if not isinstance(project_policy, ProjectPolicy):
        raise ProjectContractError("A ProjectPolicy is required")

    findings: list[SecretFinding] = []
    truncated = False
    files_scanned = 0

    for entry in snapshot.files:
        raw = _read_relative_regular_file(
            snapshot.root, entry.path, project_policy.max_file_bytes
        )
        if hashlib.sha256(raw).hexdigest() != entry.sha256:
            raise ProjectContractError(
                f"Project file changed after snapshot capture: {entry.path}"
            )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProjectContractError(
                f"Snapshot file is no longer valid UTF-8: {entry.path}"
            ) from exc
        files_scanned += 1

        for line_number, line in enumerate(text.splitlines(), start=1):
            for rule_id, pattern, severity in _PATTERNS:
                for match in pattern.finditer(line):
                    if rule_id == "credential-assignment" and _is_placeholder(match.group(1)):
                        continue
                    if len(findings) >= policy.max_findings:
                        truncated = True
                        break
                    findings.append(SecretFinding(
                        path=entry.path,
                        line=line_number,
                        rule_id=rule_id,
                        severity=severity,
                    ))
                    break
                if truncated:
                    break
            if truncated:
                break
        if truncated:
            break

    return SecretScanReport(
        snapshot_fingerprint=snapshot.fingerprint,
        files_scanned=files_scanned,
        findings=tuple(findings),
        findings_truncated=truncated,
    )
