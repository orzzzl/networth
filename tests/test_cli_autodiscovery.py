"""Adding a verb must mean adding one file and editing nothing else.

This is task 02's collision-avoidance criterion: many later tasks add a
`networth <verb>`, and if any of them has to edit a shared registry, two agents
in two worktrees collide on that file every time.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from networth import cli, commands


def test_demo_is_discovered() -> None:
    assert "demo" in cli.discover()


def test_demo_runs_through_the_parser(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["demo", "--name", "networth"]) == 0
    assert "hello networth" in capsys.readouterr().out


def test_demo_runs_as_a_subprocess() -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "networth", "demo"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "discovered" in result.stdout


def test_a_brand_new_file_becomes_a_verb_with_nothing_else_edited(tmp_path: Path) -> None:
    """The criterion, tested the only way that means anything: add a file.

    The new module is written into the installed package directory, exercised,
    and removed. Nothing else is touched — no registry, no `__init__`, no
    entry-point table.
    """
    commands_dir = Path(commands.__path__[0])
    new_verb = commands_dir / "temp_probe.py"
    assert not new_verb.exists()

    new_verb.write_text(
        "SUMMARY = 'probe'\ndef run(args):\n    print('probe ran')\n    return 0\n",
        encoding="utf-8",
    )
    try:
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "networth", "temp-probe"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert "probe ran" in result.stdout
    finally:
        new_verb.unlink()

    assert "temp-probe" not in cli.discover()


def test_underscores_in_a_module_become_dashes_in_the_verb() -> None:
    """`networth link_recover` would be a typo waiting to happen."""
    commands_dir = Path(commands.__path__[0])
    new_verb = commands_dir / "two_words.py"
    new_verb.write_text("SUMMARY = 'x'\ndef run(args):\n    return 0\n", encoding="utf-8")
    try:
        assert "two-words" in cli.discover()
        assert "two_words" not in cli.discover()
    finally:
        new_verb.unlink()


def test_a_module_missing_the_contract_is_rejected_by_name() -> None:
    commands_dir = Path(commands.__path__[0])
    bad = commands_dir / "broken_probe.py"
    bad.write_text("SUMMARY = 'no run function'\n", encoding="utf-8")
    try:
        with pytest.raises(AttributeError, match="run"):
            cli.load("broken-probe")
    finally:
        bad.unlink()


def test_no_argument_prints_help_and_fails() -> None:
    assert cli.main([]) == 2
