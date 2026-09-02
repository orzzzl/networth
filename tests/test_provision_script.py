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
Two things are executed, and both are bounded: ``bash -n``, which parses without
running — a syntax error in the artefact the owner is asked to run is exactly
the failure that must not reach him — and the ``safe_path`` helper, on
throwaway directories under ``tmp_path``.

``safe_path`` is executed because it is the one part whose *shape* proves
nothing. It exists so that a root ``chown``/``chmod`` cannot be aimed at
whatever a symlink points to, and the difference between doing that and only
appearing to is invisible to a text scan: rev 20 read as though it were safe and
was not. So those tests build the attack — a link over a victim directory —
prove a plain ``chmod`` walks straight through it, and then require the helper
not to. They are Linux-only, like ``O_PATH`` and like the host.
"""

from __future__ import annotations

import grp
import os
import pwd
import re
import stat
import subprocess
import sys
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

#: The body of the `safe_path` here-document, so the tests can run the shipped
#: bytes rather than a copy of them.
_SAFE_PATH_BODY = re.compile(r"<<'PYTHON'\n(?P<source>.*?)\nPYTHON\n", re.DOTALL)


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


@pytest.fixture(scope="module")
def safe_path_source() -> str:
    """The `safe_path` helper's Python, taken out of the script that ships it.

    Extracted rather than copied, so what the behavioural tests below execute
    is the same bytes the owner runs on his host — a copy would drift and then
    keep passing.
    """
    match = _SAFE_PATH_BODY.search(PROVISION.read_text(encoding="utf-8"))
    assert match, "the safe_path helper's here-document moved; these tests need to follow it"
    return match.group("source")


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


def test_no_ownership_or_mode_is_changed_through_a_pathname(provision: Script) -> None:
    """A shell `chown`/`chmod`/`install -d` names a path, and a name can be swapped.

    This is the regression for the defect that a guard cannot fix. `chown -h`
    refuses to follow a link, but GNU `chmod` has no such option and
    dereferences the link it is given, so "check that it is not a symlink, then
    `chmod` it" is two pathname lookups with the service account's window in
    between. The only paths still mutated by name are the two files this
    project writes in root-only directories, where there is nobody to plant a
    link; everything under `$SERVICE_HOME` or `$SECRETS_DIR` goes through
    `safe_path`, which mutates a descriptor instead.
    """
    for line in provision.code:
        mutation = _PATH_MUTATION.match(line)
        if not mutation:
            continue
        arguments = mutation.group("arguments").split(" #", 1)[0].split()
        assert arguments, f"a path mutation with no path: {line!r}"
        target = provision.resolve(_path_expression(arguments[-1]))
        assert target in ALLOWED_WRITE_TARGETS, (
            f"{target} has its ownership or mode changed by pathname, which follows a "
            f"symlink substituted after any guard; use safe_path: {line!r}"
        )

    # Non-vacuity: the assertion above is satisfied by a script that changes no
    # ownership at all, and this task's criterion (2) is that it changes some.
    call_sites = [line for line in provision.code if re.search(r"\bsafe_path\s+ensure", line)]
    assert len(call_sites) >= 3, (
        f"expected safe_path to own the service directories, the secrets directory and its "
        f"entries; found {len(call_sites)} call sites"
    )


def test_the_safe_path_helper_never_addresses_the_pathname_it_was_given(
    safe_path_source: str,
) -> None:
    """Inside the helper, the pin is the whole point: refuse links, mutate the fd.

    `O_PATH | O_NOFOLLOW` on a symlink does not fail — it returns a descriptor
    to the *link itself* — so the refusal has to be an explicit `S_ISLNK` check
    on the descriptor. Dropping that check would silently restore the
    dereference through `/proc/self/fd`.
    """
    assert "os.O_PATH | os.O_NOFOLLOW" in safe_path_source
    assert "stat.S_ISLNK(before.st_mode)" in safe_path_source, "the link refusal is gone"
    assert 'pinned = "/proc/self/fd/%d" % fd' in safe_path_source

    for call in re.findall(r"os\.(?:chown|chmod)\((?P<target>[^,]+),", safe_path_source):
        assert call == "pinned", f"os.chown/os.chmod on {call!r} rather than the pinned descriptor"

    # The read-back is a postcondition only if it cannot be aimed either.
    assert "after = os.fstat(fd)" in safe_path_source


def test_the_service_owned_child_directory_is_guarded_first(provision: Script) -> None:
    """The ordering that still matters once the mutation itself is safe.

    `safe_path` is what stops a link being followed; `require_not_symlink` is
    the readable message in front of it, and it is only worth anything if it
    runs on every iteration rather than once before the loop. `$SERVICE_HOME`
    comes first in the list because the child's path is resolved *through* the
    parent — a link at the parent has to be rejected before the child is named
    at all, and that ordering is not something a per-path descriptor can fix.
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


# --- safe_path, executed --------------------------------------------------
#
# `O_PATH` is Linux-only, and so is the host. These run in CI (ubuntu) and skip
# on the owner's Mac, which is stated rather than silently arranged: a shape
# check cannot tell whether the mutation actually landed on the descriptor, and
# that is the only thing that matters here.

linux_only = pytest.mark.skipif(
    sys.platform != "linux", reason="O_PATH and /proc/self/fd are Linux, and so is the sync host"
)


def _run_safe_path(source: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", source, *arguments], capture_output=True, text=True
    )


@pytest.fixture
def me() -> str:
    """`user:group` for the account running the tests — chowning to yourself is allowed."""
    return f"{pwd.getpwuid(os.getuid()).pw_name}:{grp.getgrgid(os.getgid()).gr_name}"


@linux_only
def test_a_plain_chmod_really_does_reach_through_the_link(tmp_path: Path) -> None:
    """The positive control, without which the refusal below proves nothing.

    If this ever fails, the fixture stopped being a dereference vector and the
    next test is passing for the wrong reason.
    """
    victim = tmp_path / "victim"
    victim.mkdir(mode=0o755)
    link = tmp_path / "link"
    link.symlink_to(victim)

    subprocess.run(["chmod", "700", str(link)], check=True)  # noqa: S603, S607

    assert stat.S_IMODE(victim.stat().st_mode) == 0o700, "plain chmod no longer follows the link"


@linux_only
def test_safe_path_refuses_a_link_and_leaves_its_target_alone(
    safe_path_source: str, tmp_path: Path, me: str
) -> None:
    """The defect, executed: the same setup the control above walks straight through."""
    victim = tmp_path / "victim"
    victim.mkdir(mode=0o755)
    link = tmp_path / "link"
    link.symlink_to(victim)

    for verb in ("ensure", "ensuredir"):
        result = _run_safe_path(safe_path_source, verb, str(link), "dir", me, "700")
        assert result.returncode != 0, f"{verb} accepted a symlink: {result.stdout!r}"
        assert "symlink" in result.stderr, result.stderr
        assert stat.S_IMODE(victim.stat().st_mode) == 0o755, (
            f"{verb} changed the link's target: the mutation followed the link"
        )
        assert link.is_symlink(), "the link itself was replaced"


@linux_only
def test_safe_path_creates_then_corrects_then_stops_writing(
    safe_path_source: str, tmp_path: Path, me: str
) -> None:
    """Create, correct, and — the part criterion (4) rests on — do nothing at all."""
    directory = tmp_path / "data"

    created = _run_safe_path(safe_path_source, "ensuredir", str(directory), "dir", me, "700")
    assert created.returncode == 0, created.stderr
    assert created.stdout.split("|")[0] == "created"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700

    directory.chmod(0o755)
    corrected = _run_safe_path(safe_path_source, "ensuredir", str(directory), "dir", me, "700")
    assert corrected.returncode == 0, corrected.stderr
    outcome, before, after = corrected.stdout.strip().split("|")
    assert (outcome, before.split()[1], after.split()[1]) == ("changed", "755", "700")

    unchanged = _run_safe_path(safe_path_source, "ensuredir", str(directory), "dir", me, "700")
    assert unchanged.returncode == 0, unchanged.stderr
    assert unchanged.stdout.split("|")[0] == "unchanged"


@linux_only
def test_safe_path_refuses_the_wrong_kind_and_never_invents_a_secret(
    safe_path_source: str, tmp_path: Path, me: str
) -> None:
    """`ensure` adopts what is there; only `ensuredir` may create, and only directories.

    The entries under `$SECRETS_DIR` are adopted with `ensure` precisely so that
    one that vanishes mid-run stops the script rather than being re-created as
    an empty file where a credential used to be.
    """
    missing = tmp_path / "gone"
    refused = _run_safe_path(safe_path_source, "ensure", str(missing), "file", me, "600")
    assert refused.returncode != 0
    assert not missing.exists(), "ensure created something"

    regular = tmp_path / "credential.env"
    regular.write_text("x", encoding="utf-8")
    mismatched = _run_safe_path(safe_path_source, "ensure", str(regular), "dir", me, "700")
    assert mismatched.returncode != 0, "a regular file was accepted as a directory"
    assert stat.S_IMODE(regular.stat().st_mode) != 0o700


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
