import pytest

from metaforge.agents.code_safety import StaticCodeReviewer


@pytest.mark.parametrize(
    "source",
    [
        "import operator\ndef solve(values):\n    read = operator.attrgetter('__class__')\n    return [read(value) for value in values]\n",
        "import operator as ops\ndef solve(values):\n    read = ops.attrgetter('__class__')\n    return [read(value) for value in values]\n",
        "import operator as ops\ndef solve(values):\n    call = ops.methodcaller('__subclasses__')\n    return [call(value) for value in values]\n",
        "from operator import attrgetter\ndef solve(values):\n    read = attrgetter('__class__')\n    return [read(value) for value in values]\n",
        "from operator import methodcaller as invoke\ndef solve(values):\n    call = invoke('__subclasses__')\n    return [call(value) for value in values]\n",
        "from operator import *\ndef solve(values):\n    read = attrgetter('__class__')\n    return [read(value) for value in values]\n",
        "from heapq import *\ndef solve(values):\n    return sorted(values)\n",
    ],
)
def test_static_reviewer_rejects_reflection_helpers_and_wildcard_imports(source):
    result = StaticCodeReviewer().review(source)
    assert result.approved is False
    assert result.findings


def test_static_reviewer_allows_non_reflective_operator_helpers():
    result = StaticCodeReviewer().review(
        "from operator import itemgetter\n"
        "def solve(values):\n"
        "    key = itemgetter(0)\n"
        "    return sorted(values, key=key)\n"
    )
    assert result.approved is True


def test_static_reviewer_rejects_excessively_complex_ast_before_evaluation():
    # About 21,000 AST nodes while remaining under the 50 KiB source cap.
    items = ",".join("(1,2)" for _ in range(7_000))
    source = "def solve(values):\n    return [" + items + "]\n"

    result = StaticCodeReviewer().review(source)

    assert result.approved is False
    assert any("AST node limit exceeded" in finding for finding in result.findings)


def test_static_reviewer_bounds_number_of_reported_findings():
    source = "import os\n" + "\n".join(
        f"def unsafe_{index}(values): return eval('1')" for index in range(200)
    )

    result = StaticCodeReviewer().review(source)

    assert result.approved is False
    assert len(result.findings) <= 101
    assert any("Additional findings omitted" in finding for finding in result.findings)
