from importlib.metadata import version

import metaforge


def test_package_version_matches_installed_distribution():
    assert metaforge.__version__ == version("metaforge")
