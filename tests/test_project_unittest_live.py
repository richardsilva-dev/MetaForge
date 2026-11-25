from __future__ import annotations

import os
from pathlib import Path

import pytest

from metaforge.projects import capture_project_snapshot
from metaforge.sandbox.policy import SandboxPolicy
from metaforge.sandbox.runner import SandboxRunner


@pytest.mark.skipif(
    os.environ.get("METAFORGE_SANDBOX_RUNTIME") != "podman",
    reason="requires the configured rootless Podman integration environment",
)
def test_project_unittest_executes_only_inside_pinned_rootless_container(tmp_path):
    root = tmp_path / "project"
    package = root / "src" / "project_pkg"
    tests = root / "tests"
    package.mkdir(parents=True)
    tests.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "core.py").write_text("def add(left, right): return left + right\n", encoding="utf-8")
    (tests / "__init__.py").write_text("", encoding="utf-8")
    test_file = tests / "test_core.py"
    test_file.write_text(
        "import unittest\nfrom project_pkg.core import add\n"
        "class CoreTests(unittest.TestCase):\n"
        "    def test_add(self): self.assertEqual(add(2, 3), 5)\n"
        "    def test_zero(self): self.assertEqual(add(0, 0), 0)\n",
        encoding="utf-8",
    )
    original = test_file.read_bytes()
    snapshot = capture_project_snapshot(root)
    policy = SandboxPolicy(
        image=os.environ["METAFORGE_SANDBOX_IMAGE"],
        runtime="podman",
        timeout_seconds=30,
    )

    result = SandboxRunner(policy).run_project_unittest(snapshot, timeout_seconds=30)

    assert result.supported is True
    assert result.executed is True
    assert result.success is True
    assert result.tests_run == 2
    assert result.failures == 0
    assert result.errors == 0
    assert test_file.read_bytes() == original
    assert result.measurement_scope == (
        "rootless_container_external_candidate_process_wall_time_including_startup"
    )
