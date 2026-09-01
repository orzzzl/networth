"""Adding a verb must mean adding one file and editing nothing else.

This is task 02's collision-avoidance criterion: many later tasks add a
`networth <verb>`, and if any of them has to edit a shared registry, two agents
in two worktrees collide on that file every time.
"""

from __future__ import annotations

import pkgutil
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


def test_a_filename_that_is_not_a_module_name_cannot_break_the_cli(tmp_path: Path) -> None:
    """One stray file used to take out every invocation, `--help` included.

    `foo-bar.py` is listed by `pkgutil.iter_modules` but can never be imported,
    so it normalized onto the same verb as `foo_bar.py` and argparse raised
    `conflicting subparser` before it could print anything. Discovery is a
    directory listing, so a file that cannot become a verb must not be able to
    break the verbs that can.
    """
    commands_dir = Path(commands.__path__[0])
    importable = commands_dir / "collision_probe.py"
    stray = commands_dir / "collision-probe.py"
    body = "SUMMARY = 'probe'\ndef run(args):\n    return 0\n"
    importable.write_text(body, encoding="utf-8")
    stray.write_text(body, encoding="utf-8")

    try:
        assert cli.discover().count("collision-probe") == 1

        result = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "networth", "--help"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert "demo" in result.stdout
        assert "not an importable module name" in result.stderr

        # The stray file is skipped; the importable one still works.
        assert cli.main(["collision-probe"]) == 0
    finally:
        importable.unlink()
        stray.unlink()

    assert "collision-probe" not in cli.discover()


def test_the_lone_stray_file_is_skipped_rather_than_offered_as_a_broken_verb(
    tmp_path: Path,
) -> None:
    """Without the importable twin there is no collision — but the verb it would
    have produced is unloadable, so offering it would only move the failure."""
    stray = Path(commands.__path__[0]) / "lonely-probe.py"
    stray.write_text("SUMMARY = 'probe'\ndef run(args):\n    return 0\n", encoding="utf-8")
    try:
        assert "lonely-probe" not in cli.discover()
        assert cli.main(["demo"]) == 0
    finally:
        stray.unlink()


def test_two_modules_claiming_one_verb_are_rejected_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The invariant `build_parser` depends on, checked rather than assumed.

    Filtering to identifiers already makes this unreachable through the file
    system — no identifier contains `-`, so nothing can merge two of them. This
    forces the collision anyway, because "unreachable" is a property of today's
    filter and the parser breaks in an unreadable way if it ever stops holding.
    """
    monkeypatch.setattr(
        pkgutil,
        "iter_modules",
        lambda path: [(None, "twin_verb", False), (None, "twin_verb", False)],
    )
    with pytest.raises(RuntimeError, match="twin-verb"):
        cli.discover()
