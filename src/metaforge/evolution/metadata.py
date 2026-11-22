from __future__ import annotations

import os
import platform
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Any


def runtime_metadata() -> dict[str, Any]:
    """Return non-secret environment metadata needed to interpret a benchmark."""
    try:
        package_version = version("metaforge")
    except PackageNotFoundError:
        package_version = "0.5.0"
    return {
        "metaforge_version": package_version,
        "python_version": platform.python_version(),
        "python_implementation": sys.implementation.name,
        "platform": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "cpu_count": os.cpu_count(),
        "source_revision": os.environ.get("GITHUB_SHA") or os.environ.get("GIT_COMMIT"),
    }
