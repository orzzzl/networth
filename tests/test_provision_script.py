"""What `scripts/provision-host.sh` may do to the owner's host, pinned in CI.

Task 28's first and third acceptance criteria are claude's half of a row the
owner executes, and both are facts about *this repository* rather than about a
run: the script must never modify ``PermitRootLogin``, and no ``PLAID_ENV``
value may be baked in anywhere. So they are checked here, where an edit that
breaks either one fails a pull request instead of an exit node.

**What these tests can and cannot establish.** They read the script as text and
assert its shape: which paths it redirects into, what it writes to the one sshd
drop-in it owns, which firewall verbs it uses. A shape check is not a proof of
runtime behaviour — a sufficiently indirect script could defeat every one of
them. What it does buy is the case that actually happens: someone adds a line to
harden root login, or hardcodes an environment while debugging, and CI stops it
in the same minute rather than the owner discovering it on a host he can no
longer log into.

The provisioning script is never *executed* here, in CI or locally. It changes
sshd, the firewall and file ownership on a live host; there is no dry mode by
design (task 28, criterion 4) and a test suite is not the place to invent one.
``bash -n`` is the exception: it parses without running, and a syntax error in
the artefact the owner is asked to run is exactly the failure that must not
reach him.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

PROVISION = REPO_ROOT / "scripts" / "provision-host.sh"
HOST_STATE = REPO_ROOT / "scripts" / "host-state.sh"

#: Files `provision-host.sh` is allowed to write. Both are files this project
#: owns: a drop-in that sets one sshd keyword, and apt's periodic switches.
#: `/etc/ssh/sshd_config` itself is deliberately absent — the script adds a
#: drop-in and never edits the host's main configuration.
ALLOWED_WRITE_TARGETS = frozenset(
    {
        "/etc/ssh/sshd_config.d/20-networth.conf",
        "/etc/apt/apt.conf.d/20auto-upgrades",
    }
)

#: Redirections that go nowhere on the filesystem.
DISCARDED = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr"})

#: `[[ … ]]` and `(( … ))` are blanked before redirections are collected: both
#: use `>` as a comparison operator, and neither can contain a file
#: redirection. Without this, `((major > MINIMUM))` reads as a write.
_COMPARISON = re.compile(r"\(\(.*?\)\)|\[\[.*?\]\]", re.DOTALL)

#: A `>` or `>>` that is not part of `2>`, `>&2`, `<<`, `->`, or `=>`.
_REDIRECT = re.compile(r"(?<![0-9<>&=|-])>>?\s*(?P<target>[^\s;|&)}]+)")

#: `<<DELIM` / `<<-DELIM` / `<<'DELIM'`, but never a `<<<` here-string.
_HEREDOC = re.compile(
    r"(?<!<)<<-?\s*(?P<quote>['\"]?)(?P<delimiter>[A-Za-z_][A-Za-z0-9_]*)(?P=quote)"
)

#: `NAME=value` at the start of a line, optionally `readonly`. A value spanning
#: several lines is captured only as far as its first line, which is fine for
#: what these constants are used for here: resolving redirection targets.
_ASSIGNMENT = re.compile(r"^(?:readonly\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$")

#: `passwd` invoked as a command — not `getent passwd`, and not the read-only
#: `passwd -S` that reports whether an account has a usable password.
_PASSWD_COMMAND = re.compile(r"(?:^|[;&|]|\$\(|\bsudo\s+)\s*passwd\b(?!\s+-S)")

#: `date` invoked as a command, so that the word inside `Update-Package-Lists`
#: does not read as a clock.
_DATE_COMMAND = re.compile(r"(?:^|[;&|(`]|\$\()\s*date\b", re.MULTILINE)

#: A command that changes a path's ownership or mode, or creates a directory.
#: All three resolve symlinks in their final argument, which is the path.
_PATH_MUTATION = re.compile(r"^\s*(?:chown|chmod|install\s+-d)\b(?P<arguments>.*)$")

#: A path proven not to be a symlink — either the helper, or the inline `-L`
#: test used for the entries inside the secrets directory.
_SYMLINK_GUARD = re.compile(r"(?:require_not_symlink\s+|-L\s+)(?P<path>\S+)")


@dataclass(frozen=True)
class Heredoc:
    """One here-document: where it opened, and whether it lands in a file."""

    delimiter: str
    opener: str
    body: str

    @property
    def goes_to_a_file(self) -> bool:
        return bool(_redirect_targets(self.opener))


@dataclass(frozen=True)
class Script:
    """A shell script split into the parts that mean different things.

    `code` excludes comments and here-document bodies, because those two are
    text — a comment describing `PermitRootLogin` and a printed instruction
    telling the owner how to set it himself are not the script setting it.
    """

    path: Path
    text: str
    code: tuple[str, ...]
    heredocs: tuple[Heredoc, ...]
    constants: dict[str, str]

    def resolve(self, value: str) -> str:
        """Substitute the script's own literal constants into `value`."""
        for _ in range(4):  # constants may reference constants; 4 is generous
            replaced = re.sub(
                r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?",
                lambda match: self.constants.get(match.group(1), match.group(0)),
                value,
            )
            if replaced == value:
                return replaced
            value = replaced
        return value


def _unquote(value: str) -> str:
    """Drop one layer of matching surrounding quotes, if there is one."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _redirect_targets(line: str) -> list[str]:
    return [match.group("target") for match in _REDIRECT.finditer(_COMPARISON.sub("", line))]


def _path_expression(token: str) -> str:
    """One comparable spelling for `"$directory"`, `${DATA_DIR}` and `$entry`."""
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", r"$\1", _unquote(token))


def _load(path: Path) -> Script:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    code: list[str] = []
    heredocs: list[Heredoc] = []
    constants: dict[str, str] = {}

    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if line.lstrip().startswith("#"):
            continue
        code.append(line)

        assignment = _ASSIGNMENT.match(line.strip())
        if assignment:
            constants[assignment.group("name")] = _unquote(assignment.group("value"))

        opener = _HEREDOC.search(line)
        if not opener:
            continue
        delimiter = opener.group("delimiter")
        body: list[str] = []
        while index < len(lines) and lines[index].strip() != delimiter:
            body.append(lines[index])
            index += 1
        index += 1  # the delimiter line itself
        heredocs.append(Heredoc(delimiter=delimiter, opener=line, body="\n".join(body)))

    return Script(
        path=path,
        text=text,
        code=tuple(code),
        heredocs=tuple(heredocs),
        constants=constants,
    )


@pytest.fixture(scope="module")
def provision() -> Script:
    return _load(PROVISION)


@pytest.fixture(scope="module")
def host_state() -> Script:
    return _load(HOST_STATE)


# --- criterion (1): PermitRootLogin is never modified -----------------------


def test_the_only_sshd_setting_written_is_password_authentication(provision: Script) -> None:
    """The drop-in's body is a constant, so what lands in the file is greppable.

    This is criterion (1) at its narrowest: not "the script currently does not
    set `PermitRootLogin`", but "the only thing it can write into sshd's
    configuration is this one line".
    """
    body = provision.constants["SSHD_DROPIN_BODY"]

    assert body == "PasswordAuthentication no"
    assert "permitrootlogin" not in body.lower()


def test_permit_root_login_appears_only_where_it_is_read_or_printed(provision: Script) -> None:
    """No executed line touching that keyword may also write, chmod or reload.

    The keyword is legitimately present three ways: a comment, the `sshd -T`
    read that reports its current value, and the proposal the owner is shown.
    None of those may be a change.
    """
    mutating = ("tee ", "sed -i", "chmod", "chown", "systemctl", "ufw ", "install ")

    for line in provision.code:
        if "permitrootlogin" not in line.lower():
            continue
        assert not _redirect_targets(line), f"a line writes while naming PermitRootLogin: {line!r}"
        for verb in mutating:
            assert verb not in line, f"a line mutates while naming PermitRootLogin: {line!r}"


def test_the_permit_root_login_proposal_is_printed_not_applied(provision: Script) -> None:
    """The proposal contains a shell command; it must reach stdout, not a file.

    It is written as something the owner can copy precisely because he is the
    one who decides. A here-document that named the keyword and was redirected
    into a config file would be the same text doing the opposite thing.
    """
    naming = [doc for doc in provision.heredocs if "permitrootlogin" in doc.body.lower()]

    assert naming, "the proposal that DESIGN.md §19 step 3.1 requires is missing"
    for doc in naming:
        assert not doc.goes_to_a_file, (
            f"the {doc.delimiter} here-document is redirected into a file"
        )


def test_the_script_writes_only_the_two_files_it_owns(provision: Script) -> None:
    """Every redirection target, resolved through the script's own constants.

    An unrecognised target fails rather than being ignored: the point of this
    test is that adding a write to the owner's host is a reviewed event.
    """
    for line in provision.code:
        for raw in _redirect_targets(line):
            target = provision.resolve(raw.strip("'\""))
            if target in DISCARDED or target.startswith("&"):
                continue
            assert target in ALLOWED_WRITE_TARGETS, f"unexpected write to {target!r} in: {line!r}"


def test_the_main_sshd_config_is_never_edited(provision: Script) -> None:
    """`/etc/ssh/sshd_config` is the owner's file; we add a drop-in beside it."""
    for line in provision.code:
        assert "sed -i" not in line, f"in-place edit in: {line!r}"
        for raw in _redirect_targets(line):
            assert provision.resolve(raw.strip("'\"")) != "/etc/ssh/sshd_config"


# --- criterion (3): /etc/networth/plaid.env, and no hardcoded PLAID_ENV -----


def test_no_plaid_env_value_is_baked_into_the_shipped_code() -> None:
    """`PLAID_ENV` is read from the credential file, never written by us.

    `networth/` and `scripts/` are what runs on the sync host. A constant here
    is what makes a Sandbox rehearsal one edit away from Production (§15), so
    the assertion is against the assignment shape, not the name — the name is
    discussed all over the code that reads it.
    """
    assignment = re.compile(r"\bPLAID_ENV\s*=")
    offenders: list[str] = []

    for directory in ("networth", "scripts"):
        for path in sorted((REPO_ROOT / directory).rglob("*")):
            if not path.is_file():
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            offenders += [
                f"{path.relative_to(REPO_ROOT)}:{number}"
                for number, line in enumerate(content.splitlines(), start=1)
                if assignment.search(line)
            ]

    assert offenders == [], f"PLAID_ENV is assigned a value in: {offenders}"


def test_provisioning_never_writes_into_the_secrets_directory(provision: Script) -> None:
    """It fixes ownership and modes there; the owner installs the files.

    Criterion (3) says configuration is *read* from `/etc/networth/plaid.env`.
    A provisioning script that could also write that path would be a second
    author for the one file no agent may compose (AGENTS.md rule 3).
    """
    for line in provision.code:
        for raw in _redirect_targets(line):
            target = provision.resolve(raw.strip("'\""))
            assert not target.startswith("/etc/networth"), f"writes into the secrets dir: {line!r}"

    assert "plaid.env" not in provision.text, "the script names a credential file it must not touch"


# --- the "must not" list from task 28 ---------------------------------------


def test_no_port_but_ssh_is_opened(provision: Script) -> None:
    """§8.4: v0 has no public inbound service, so 22/tcp is the whole list."""
    allows = [line.strip() for line in provision.code if re.search(r"\bufw allow\b", line)]

    assert allows, "the firewall step is missing"
    for line in allows:
        assert "22/tcp" in line, f"a rule other than SSH is added: {line!r}"


def test_no_firewall_rule_is_ever_removed(provision: Script) -> None:
    """Rules that predate this project belong to the host, not to us.

    41641/udp — Tailscale's direct-connection port — is on the owner's exit node
    today. A "tidy up everything that is not SSH" step would take his VPN's
    direct path away and leave a working-but-relayed tunnel nobody would connect
    to this script.
    """
    for verb in ("ufw delete", "ufw reset", "ufw disable", "ufw --force reset"):
        assert verb not in provision.text, f"{verb!r} appears in the script"


def test_no_application_unit_is_installed(provision: Script) -> None:
    """Task 16 owns the units end to end; 28 stops at the base host."""
    for line in provision.code:
        assert "/etc/systemd" not in line, f"writes a unit: {line!r}"
        assert not re.search(r"systemctl\s+(enable|start)\s+networth", line), (
            f"starts ours: {line!r}"
        )


def test_nothing_asks_for_a_password(provision: Script) -> None:
    """§15.1's standing rule, and the script must also run unattended."""
    assert not re.search(r"\bread\s+-[a-z]*s", provision.text), "reads a secret from the terminal"
    for line in provision.code:
        assert not _PASSWD_COMMAND.search(line), f"changes or sets a password: {line!r}"


# --- root must not follow a link the service account can plant ---------------
#
# Found by codex reviewing PR #34. After run 1 the unprivileged `networth`
# account owns `$SERVICE_HOME`, so it can replace `$DATA_DIR` with a symlink to
# anywhere on the host; `-d`, `chown` and `chmod` all resolve it, so the owner's
# next run would retarget root's mutation. These three tests are the regression:
# compromise of the daemon account must not become a root filesystem primitive.


def test_every_ownership_change_refuses_to_dereference(provision: Script) -> None:
    """`chown -h` acts on a link, never through it.

    The `-L` guards below are what actually stop this, and they are checked
    separately. This is the second layer: a path that turned into a symlink
    between the guard and the mutation still cannot aim the `chown` elsewhere.
    """
    for line in provision.code:
        if not re.match(r"\s*chown\b", line):
            continue
        assert re.match(r"\s*chown\s+(?:-h|--no-dereference)\b", line), (
            f"a chown that follows symlinks: {line!r}"
        )


def test_no_path_is_chowned_or_chmodded_through_a_symlink(provision: Script) -> None:
    """Every mutated path is proven a non-symlink first, and *earlier* in the file.

    The two files the script writes itself are exempt: they are the ones
    `ALLOWED_WRITE_TARGETS` already pins, and both live in directories only root
    can write, so there is nobody to plant the link.
    """
    guards: dict[str, int] = {}
    for index, line in enumerate(provision.code):
        for match in _SYMLINK_GUARD.finditer(line):
            guards.setdefault(_path_expression(match.group("path")), index)

    for index, line in enumerate(provision.code):
        mutation = _PATH_MUTATION.match(line)
        if not mutation:
            continue
        arguments = mutation.group("arguments").split(" #", 1)[0].split()
        assert arguments, f"a path mutation with no path: {line!r}"
        target = _path_expression(arguments[-1])
        if provision.resolve(target) in ALLOWED_WRITE_TARGETS:
            continue
        assert target in guards, (
            f"{target} is mutated but never checked for being a symlink: {line!r}"
        )
        assert guards[target] < index, (
            f"{target} is mutated at line {index} before its symlink check at {guards[target]}"
        )


def test_the_service_owned_child_directory_is_guarded_first(provision: Script) -> None:
    """The loop's first statement is the guard, so it covers every iteration.

    `$DATA_DIR` is the dangerous one — it sits *inside* a directory the service
    account owns — and it is only guarded because the check is the first thing
    in the loop body rather than a one-off before it. `$SERVICE_HOME` comes
    first in the list for the same reason: the child's path is resolved through
    the parent, so a link at the parent has to be rejected before the child is
    named at all.
    """
    headers = [
        index for index, line in enumerate(provision.code) if line.startswith("for directory in ")
    ]

    assert len(headers) == 1, "the service-directory loop moved; this test needs to follow it"
    header = provision.code[headers[0]]
    assert "$SERVICE_HOME" in header and "$DATA_DIR" in header
    assert header.index("$SERVICE_HOME") < header.index("$DATA_DIR"), "the parent must come first"

    body = (line.strip() for line in provision.code[headers[0] + 1 :] if line.strip())
    assert next(body) == 'require_not_symlink "$directory"', (
        "the symlink check is not the first thing the loop does"
    )


# --- the read-only companion ------------------------------------------------


def test_host_state_capture_changes_nothing(host_state: Script) -> None:
    """`host-state.sh` is what anyone may run, so it must only ever read.

    Criterion (4) needs three captures — before run 1, between the runs, after
    run 2 — and the middle one sits *inside* the owner's sequence. Only a
    capture that cannot alter what it measures may be interleaved with the two
    provisioning passes like that, and it is also what lets an agent take one
    against the live host at any time.
    """
    forbidden = (
        "chmod",
        "chown",
        "useradd",
        "mkdir",
        "rm ",
        "install -d",
        "apt-get",
        "ufw allow",
        "ufw enable",
        "ufw delete",
        "systemctl start",
        "systemctl stop",
        "systemctl enable",
        "systemctl reload",
        "systemctl restart",
    )

    for line in host_state.code:
        for verb in forbidden:
            assert verb not in line, f"host-state.sh is not read-only: {line!r}"
        for raw in _redirect_targets(line):
            target = host_state.resolve(raw.strip("'\""))
            assert target in DISCARDED or target.startswith("&"), f"writes {target!r}: {line!r}"


def test_host_state_output_is_stable_across_runs(host_state: Script) -> None:
    """No clock and no pid in the snapshot, or every diff is non-empty.

    An idempotence criterion measured with a tool that changes its own output
    between runs cannot fail honestly — it can only be argued about.
    """
    assert not _DATE_COMMAND.search(host_state.text), (
        "a timestamp would differ between two captures"
    )
    assert "pid=[0-9]" in host_state.text, "pids must be stripped from the listener list"


# --- both scripts -----------------------------------------------------------


@pytest.mark.parametrize("path", [PROVISION, HOST_STATE], ids=lambda path: str(path.name))
def test_script_parses_and_is_runnable(path: Path) -> None:
    """`bash -n` parses without executing. The owner runs this file by hand.

    It caught a real defect while this script was being written: an unquoted
    `[` inside a parameter expansion opened a pattern bracket and swallowed the
    rest of the file, on bash 3.2 only. CI runs bash 5, so a version-specific
    parse error can pass review on one machine and fail on another.
    """
    assert path.stat().st_mode & 0o111, f"{path.name} is not executable"

    text = path.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash\n")
    assert "\nset -euo pipefail\n" in text

    result = subprocess.run(  # noqa: S603
        ["bash", "-n", str(path)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
