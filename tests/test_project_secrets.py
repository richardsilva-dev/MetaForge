import pytest

from metaforge.projects import ProjectContractError, capture_project_snapshot
from metaforge.projects.secrets import (
    SecretScanPolicy,
    scan_project_secrets,
)


def test_secret_scan_detects_common_secret_patterns_without_retaining_values(tmp_path):
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    secret_values = {
        "openai": "sk-proj-" + "A" * 48,
        "github": "ghp_" + "B" * 36,
        "stripe": "sk_live_" + "C" * 24,
        "groq": "gsk_" + "D" * 28,
        "huggingface": "hf_" + "E" * 28,
        "aws": "AKIA" + "F" * 16,
        "google": "AIza" + "G" * 35,
        "slack": "xoxb-" + "H" * 28,
        "jwt": "eyJ" + "I" * 16 + "." + "J" * 16 + "." + "K" * 16,
        "generic": "password = 'generic-secret-value-123'\nAWS_SECRET_ACCESS_KEY = 'aws-secret-value-123456' ",
        "url": "https://service-user:service-password@example.invalid/path",
    }
    source = "\n".join([
        f"OPENAI_API_KEY = '{secret_values['openai']}'",
        f"GITHUB_TOKEN = '{secret_values['github']}'",
        f"STRIPE_SECRET_KEY = '{secret_values['stripe']}'",
        f"GROQ_API_KEY = '{secret_values['groq']}'",
        f"HF_TOKEN = '{secret_values['huggingface']}'",
        f"AWS_ACCESS_KEY_ID = '{secret_values['aws']}'",
        f"GOOGLE_API_KEY = '{secret_values['google']}'",
        f"SLACK_TOKEN = '{secret_values['slack']}'",
        f"JWT = '{secret_values['jwt']}'",
        secret_values["generic"],
        secret_values["url"],
    ])
    (root / "src" / "settings.py").write_text(source, encoding="utf-8")
    snapshot = capture_project_snapshot(root)

    report = scan_project_secrets(snapshot)

    assert report.no_detected_secrets is False
    assert report.files_scanned == 1
    rule_ids = {finding.rule_id for finding in report.findings}
    assert {
        "openai-style-api-key",
        "github-token",
        "stripe-secret-key",
        "groq-api-key",
        "huggingface-token",
        "aws-access-key-id",
        "google-api-key",
        "slack-token",
        "jwt-like-token",
        "credential-assignment",
        "credential-url",
    }.issubset(rule_ids)

    serialized = repr(report) + str(report.to_dict())
    for secret in secret_values.values():
        assert secret not in serialized


def test_secret_scan_ignores_common_placeholders_and_environment_references(tmp_path):
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "settings.py").write_text(
        "\n".join([
            'api_key = "YOUR_API_KEY"',
            'password = "changeme"',
            'client_secret = os.getenv("CLIENT_SECRET")',
            'auth_token = process.env.AUTH_TOKEN',
            'private_key = None',
            'SECRET = "standalone-secret-value-987"',
        ]),
        encoding="utf-8",
    )
    report = scan_project_secrets(capture_project_snapshot(root))

    assert report.no_detected_secrets is False
    assert any(item.rule_id == "credential-assignment" for item in report.findings)


def test_secret_scan_detects_private_key_header(tmp_path):
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "keys.md").write_text(
        "Example block:\n-----BEGIN RSA PRIVATE KEY-----\nredacted\n",
        encoding="utf-8",
    )
    report = scan_project_secrets(capture_project_snapshot(root))
    assert any(item.rule_id == "private-key-header" and item.line == 2 for item in report.findings)


def test_secret_scan_is_bound_to_snapshot_hash_and_fails_on_stale_file(tmp_path):
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    source_file = root / "src" / "module.py"
    source_file.write_text("value = 1\n", encoding="utf-8")
    snapshot = capture_project_snapshot(root)
    source_file.write_text("value = 2\n", encoding="utf-8")

    with pytest.raises(ProjectContractError, match="changed after snapshot"):
        scan_project_secrets(snapshot)


def test_secret_scan_truncation_fails_closed(tmp_path):
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "settings.py").write_text(
        "\n".join(f'api_key = "secret-value-{index:04d}"' for index in range(5)),
        encoding="utf-8",
    )
    report = scan_project_secrets(
        capture_project_snapshot(root),
        SecretScanPolicy(max_findings=2),
    )

    assert len(report.findings) == 2
    assert report.findings_truncated is True
    assert report.no_detected_secrets is False


def test_secret_scan_policy_rejects_boolean_and_out_of_range_budget():
    with pytest.raises(ProjectContractError):
        SecretScanPolicy(max_findings=True)
    with pytest.raises(ProjectContractError):
        SecretScanPolicy(max_findings=0)


def test_empty_snapshot_does_not_pass_secret_scan_gate(tmp_path):
    root = tmp_path / "empty-project"
    root.mkdir()
    report = scan_project_secrets(capture_project_snapshot(root))
    assert report.files_scanned == 0
    assert report.no_detected_secrets is False
