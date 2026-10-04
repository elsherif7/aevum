"""scripts/clean.py removes build artifacts and nothing else."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "clean.py"


@pytest.fixture
def clean_module(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("aevum_clean_script", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    return module


def _make_project(root: Path) -> tuple[list[Path], list[Path]]:
    doomed = [
        root / "build" / "lib" / "x.py",
        root / "dist" / "aevum-1.0.tar.gz",
        root / "aevum.egg-info" / "PKG-INFO",
        root / "__editable__.aevum-1.0.pth",
        root / "pkg" / "__pycache__" / "mod.cpython-311.pyc",
        root / "stray.pyc",
        root / "pkg" / "old.pyo",
    ]
    kept = [root / "README.md", root / "pkg" / "mod.py", root / "tests" / "test_x.py"]
    for path in doomed + kept:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    return doomed, kept


def test_clean_removes_build_artifacts_and_keeps_sources(clean_module, tmp_path, capsys):
    doomed, kept = _make_project(tmp_path)
    clean_module.clean()
    assert not [p for p in doomed if p.exists()]
    assert all(p.exists() for p in kept)
    assert not (tmp_path / "build").exists()
    assert not (tmp_path / "pkg" / "__pycache__").exists()
    out = capsys.readouterr().out
    assert "removed" in out
    assert "Done." in out


def test_clean_on_a_clean_tree_is_a_no_op(clean_module, tmp_path, capsys):
    (tmp_path / "keep.py").write_text("x")
    clean_module.clean()
    assert (tmp_path / "keep.py").exists()
    out = capsys.readouterr().out
    assert "removed" not in out
    assert "Done." in out


def test_clean_ignores_a_path_that_does_not_exist(clean_module, tmp_path, capsys):
    clean_module._rm(tmp_path / "missing")
    assert capsys.readouterr().out == ""
