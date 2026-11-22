import sys

import pytest

from metaforge.cli import main


def test_code_evolve_requires_local_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["metaforge", "code-evolve"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


def test_code_evolve_requires_explicit_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "local-test-placeholder")
    monkeypatch.delenv("METAFORGE_AGENT_MODEL", raising=False)
    monkeypatch.setattr(sys, "argv", ["metaforge", "code-evolve"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


def test_code_evolve_requires_pinned_sandbox_image(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "local-test-placeholder")
    monkeypatch.setenv("METAFORGE_AGENT_MODEL", "scripted-test-model")
    monkeypatch.delenv("METAFORGE_SANDBOX_IMAGE", raising=False)
    monkeypatch.setattr(sys, "argv", ["metaforge", "code-evolve"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
