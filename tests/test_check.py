"""scripts/check.py runs every check and fails if any of them fails."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check.py"


@pytest.fixture
def check_module():
    spec = importlib.util.spec_from_file_location("aevum_check_script", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_check_runs_even_after_a_failure(check_module, monkeypatch, capsys):
    ran = []

    def fake_run_check(name, args):
        ran.append(name)
        return name != "mypy"

    monkeypatch.setattr(check_module, "run_check", fake_run_check)
    assert check_module.main() == 1
    assert ran == [name for name, _ in check_module.CHECKS]
    out = capsys.readouterr().out
    assert "FAILED  mypy" in out
    assert "ok      ruff" in out


def test_exit_code_is_zero_when_everything_passes(check_module, monkeypatch):
    monkeypatch.setattr(check_module, "run_check", lambda name, args: True)
    assert check_module.main() == 0


def test_run_check_reports_a_failing_command(check_module, monkeypatch, capsys):
    monkeypatch.setattr(check_module, "ROOT", Path(__file__).parent)
    assert check_module.run_check("fails", ["no_such_module_xyz"]) is False
    assert "fails: FAILED" in capsys.readouterr().out
