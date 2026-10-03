"""
The supported Python version is stated in several places. These tests keep them
in step, so raising the minimum in one place can't leave the others behind.
"""
from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _minimum() -> tuple[int, int]:
    spec = PYPROJECT["project"]["requires-python"]
    m = re.fullmatch(r">=\s*(\d+)\.(\d+)", spec)
    assert m, f"requires-python should be a plain lower bound like '>=3.11', got {spec!r}"
    return int(m[1]), int(m[2])


def _classifier_minors() -> list[int]:
    return sorted(
        int(m[1])
        for c in PYPROJECT["project"]["classifiers"]
        if (m := re.fullmatch(r"Programming Language :: Python :: 3\.(\d+)", c))
    )


def test_requires_python_has_no_upper_cap():
    # Capping (e.g. '<3.15') makes pip refuse to install on newer Pythons for no reason.
    assert "<" not in PYPROJECT["project"]["requires-python"]
    _minimum()


def test_ruff_targets_the_minimum_version():
    major, minor = _minimum()
    assert PYPROJECT["tool"]["ruff"]["target-version"] == f"py{major}{minor}"


def test_mypy_checks_against_the_minimum_version():
    major, minor = _minimum()
    assert PYPROJECT["tool"]["mypy"]["python_version"] == f"{major}.{minor}"


def test_classifiers_start_at_the_minimum_with_no_gaps():
    _, minor = _minimum()
    minors = _classifier_minors()
    assert minors, "no 'Programming Language :: Python :: 3.X' classifiers"
    assert minors[0] == minor, f"classifiers start at 3.{minors[0]} but requires-python says 3.{minor}"
    assert minors == list(range(minors[0], minors[-1] + 1)), f"gap in the classifiers: {minors}"
    assert "Programming Language :: Python :: 3" in PYPROJECT["project"]["classifiers"]


def test_readme_states_the_minimum_version():
    major, minor = _minimum()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"Python {major}.{minor}" in readme
    older = [m for m in re.findall(r"Python 3\.(\d+)\+?", readme) if int(m) < minor]
    assert not older, f"README mentions older Python versions: 3.{older}"


def _python_files() -> list[Path]:
    files = [ROOT / "aevum.py"]
    for folder in ("aevum_pkg", "scripts", "tests"):
        files += sorted((ROOT / folder).glob("*.py"))
    return files


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_source_parses_with_the_minimum_versions_grammar(path):
    # Catches newer *syntax* (match, PEP 695 'type X = ...', ...) sneaking in. It does
    # not catch newer library calls: running the tests on the oldest supported Python
    # is what proves those.
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=_minimum())


def test_version_has_a_single_source():
    project = PYPROJECT
    assert "version" not in project["project"]
    assert "version" in project["project"]["dynamic"]
    assert project["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "aevum_pkg.__version__"}


def test_version_string_is_a_plain_release_number():
    from aevum_pkg import __version__
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)


def test_build_backend_is_new_enough_for_spdx_license():
    # `license = "GPL-3.0-only"` (a plain SPDX string) needs setuptools 77+;
    # older versions fail the build with a configuration error.
    assert "setuptools>=77" in PYPROJECT["build-system"]["requires"]


def test_mypy_checks_every_module():
    files = PYPROJECT["tool"]["mypy"]["files"]
    assert "aevum_pkg" in files
    assert "tests" in files
    assert PYPROJECT["tool"]["mypy"]["check_untyped_defs"] is True
