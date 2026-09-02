"""What the owner is asked to paste, pinned in CI.

`DESIGN.md` §19 step 3.1 is an artefact he executes, exactly as
`scripts/provision-host.sh` is — and it is the half nothing checked. Rev 21
spent three review rounds making that sequence extract *reviewed* bytes and
report a *real* exit status, and shipped it with no identity on any of its six
remote commands. On `zelengs-macbook-air-2` there is no `~/.ssh/config`, no
default identity file and no key in the running agent, so the paste would have
stopped at its first `scp` with `Permission denied (publickey)`.

Reading the block is what missed it three times, so what is asserted here is a
property of the block rather than its prose: **every command in it that reaches
the host authenticates the way step 3.1 has to on the machine it names.** Each
clause of that is here because the first version of this module left it out and
a real authentication-breaking edit went through:

- the identity resolves to step 1a's **exact** path, not to anything whose
  basename ends the same way — `/tmp/networth-vps.key` also does, and is not a
  key the owner installed;
- it resolves through **only the assignments a shell would already have made**
  when that command runs — a `vps_key=` line moved below its uses expands to
  nothing in the real shell, so it must resolve to nothing here;
- and the command carries `IdentitiesOnly=yes`, without which a loaded agent
  can authenticate a command whose `-i` is wrong, which is what makes the first
  two clauses mean anything at all.

This is a shape check, with the usual limits: it cannot prove the key is on the
host, which only the host can answer, and it reads a block as a flat sequence,
so an assignment nested inside its own subshell would count as in scope here
while the real shell discarded it. The case it buys is the one that happened —
a remote command added or edited without a usable identity.

The vacuity guard and the four controls are not decoration. Every assertion
about the shipped block is "no command is missing something", which is equally
what an empty list of commands says and what a predicate that never fires says.
So the controls feed the **shipped** text through the exact edits that must not
pass, rather than a hand-written imitation of it that can drift away from it.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable

from tests.conftest import REPO_ROOT

DESIGN = REPO_ROOT / "DESIGN.md"

#: The body of a fenced block, fence to fence. The fences are allowed to be
#: indented because §19's sequence sits inside a numbered step and is — the
#: first version of this module anchored on column 0 and found nothing.
_FENCED = re.compile(r"^[ \t]*```[^\n]*\n(?P<body>.*?)^[ \t]*```", re.MULTILINE | re.DOTALL)

#: A line that opens a connection to a host. Anchored, so `git … ssh` in the
#: middle of a line is not one and a prose mention is never reached at all.
_REMOTE = re.compile(r"^(ssh|scp)\s")

#: A line that does nothing but assign — the form the sequence names the key in.
#: `export name=…` and `name=… command` are deliberately not matched: an
#: assignment this misses leaves its variable unresolved, which fails.
_ASSIGNMENT = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(\S+)\s*$")

#: `$name` or `${name}` and nothing else. A path with a variable embedded in it
#: is not step 1a's path as step 1a spells it, and is meant not to resolve.
_REFERENCE = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")

#: Step 1a, its heading to the next one — where the key's path is established.
_STEP_1A = re.compile(r"^\*\*Step 1a\b.*?(?=^\*\*Step 1b\b)", re.MULTILINE | re.DOTALL)

#: The key §19 step 1a generates and the owner installs, spelled as step 1a
#: spells it. `test_step_1a_still_installs_the_key_the_sequence_names` is what
#: keeps this constant and that step from drifting apart.
KEY = "~/agents/secrets/networth-vps.key"

#: Normalised the way `_options` normalises, so that `-o IdentitiesOnly=yes`,
#: `-oIdentitiesOnly=yes` and `-o "IdentitiesOnly yes"` — one instruction to
#: `ssh`, three spellings — are one string here too.
IDENTITIES_ONLY = "identitiesonly=yes"


def runbook_blocks(document: str) -> list[str]:
    """Every fenced block that reaches a host — the ones the owner pastes."""
    return [m.group("body") for m in _FENCED.finditer(document) if "root@" in m.group("body")]


def _literal(value: str) -> str:
    """`"~/x"` and `~/x` are the same path; quoting it is not a different key."""
    tokens = shlex.split(value)
    return tokens[0] if len(tokens) == 1 else value


def _resolved(block: str) -> list[tuple[str, dict[str, str]]]:
    """Each remote command with the variables a shell would have set *by then*.

    Reading assignments in order is the point. Collecting them from the whole
    block first resolved a `vps_key=` line sitting *below* every use of it — an
    edit that leaves the real shell expanding an unset variable to nothing.
    """
    commands: list[tuple[str, dict[str, str]]] = []
    variables: dict[str, str] = {}
    for line in block.splitlines():
        if assignment := _ASSIGNMENT.match(line):
            variables[assignment.group(1)] = _literal(assignment.group(2))
        elif _REMOTE.match(line.strip()):
            commands.append((line.strip(), dict(variables)))
    return commands


def remote_commands(block: str) -> list[str]:
    """The commands that reach a host, in the order the shell would run them."""
    return [command for command, _ in _resolved(block)]


def _setting(option: str) -> str:
    """`IdentitiesOnly=yes` and `identitiesonly yes` are one setting to `ssh`."""
    return re.sub(r"\s*=\s*|\s+", "=", option.strip()).lower()


def _options(tokens: list[str]) -> set[str]:
    """Every `-o` setting a command carries, in either spelling, normalised."""
    values = []
    for index, token in enumerate(tokens):
        if token == "-o" and index + 1 < len(tokens):
            values.append(tokens[index + 1])
        elif token.startswith("-o") and token != "-o":
            values.append(token[2:])
    return {_setting(value) for value in values}


def identity_of(command: str, variables: dict[str, str]) -> str | None:
    """The `-i` argument of one remote command, with `"$name"` resolved.

    `None` means the command names no identity, or names a variable nothing has
    assigned yet — both of which are the failure this module exists for, not an
    absence of information.
    """
    tokens = shlex.split(command)
    if "-i" not in tokens or tokens.index("-i") + 1 == len(tokens):
        return None
    argument = tokens[tokens.index("-i") + 1]
    if reference := _REFERENCE.match(argument):
        return variables.get(reference.group(1))
    return argument


def unauthenticated(block: str) -> list[str]:
    """Commands in `block` that would not reach the host as step 1a's key."""
    return [
        command
        for command, variables in _resolved(block)
        if identity_of(command, variables) != KEY
        or IDENTITIES_ONLY not in _options(shlex.split(command))
    ]


def the_provisioning_sequence() -> str:
    """§19 step 3.1's block — or a failure naming it, never silence."""
    document = DESIGN.read_text(encoding="utf-8")
    blocks = [b for b in runbook_blocks(document) if "provision-host.sh" in b]
    assert len(blocks) == 1, "DESIGN.md section 19 step 3.1's sequence was not found"
    return blocks[0]


def _with_identity(command: str, argument: str | None) -> str:
    """One remote command, its `-i` argument replaced — or `-i` removed.

    Token surgery rather than a string replacement, and the same for
    `_without_identities_only`. A control that edits one hard-coded spelling
    stops editing anything the day the block is written in another — and then
    fails, reporting a sequence that authenticates perfectly well as broken.
    """
    tokens = shlex.split(command)
    assert "-i" in tokens, f"nothing to rewrite, this command names no identity: {command}"
    index = tokens.index("-i")
    assert index + 1 < len(tokens), f"`-i` has no argument: {command}"
    return shlex.join(
        tokens[:index] + ([] if argument is None else ["-i", argument]) + tokens[index + 2 :]
    )


def _without_identities_only(command: str) -> str:
    """One remote command with its `IdentitiesOnly` setting dropped."""
    tokens = shlex.split(command)
    kept: list[str] = []
    index = 0
    while index < len(tokens):
        token, following = tokens[index], tokens[index + 1] if index + 1 < len(tokens) else ""
        if token == "-o" and _setting(following) == IDENTITIES_ONLY:
            index += 2
        elif token.startswith("-o") and token != "-o" and _setting(token[2:]) == IDENTITIES_ONLY:
            index += 1
        else:
            kept.append(token)
            index += 1
    assert len(kept) < len(tokens), f"nothing to drop, this command does not set it: {command}"
    return shlex.join(kept)


def _rewrite(block: str, edit: Callable[[str], str], limit: int | None = None) -> str:
    """`block` with `edit` applied to its first `limit` remote commands."""
    lines: list[str] = []
    edited = 0
    for line in block.splitlines():
        if _REMOTE.match(line.strip()) and (limit is None or edited < limit):
            lines.append(line[: len(line) - len(line.lstrip())] + edit(line.strip()))
            edited += 1
        else:
            lines.append(line)
    assert edited == (limit or len(remote_commands(block))), "the sequence lost commands"
    return "\n".join(lines)


def _assert_nothing_authenticates(mutated: str) -> None:
    """A control's shared conclusion: every command rejected, none removed.

    The second half matters as much as the first. A mutation that deleted the
    commands instead of breaking them would satisfy "all of them are rejected"
    with an empty list, and prove nothing about the predicate.
    """
    commands = remote_commands(mutated)
    assert len(commands) == len(remote_commands(the_provisioning_sequence()))
    assert unauthenticated(mutated) == commands


def test_the_provisioning_sequence_is_present_and_is_what_is_being_checked() -> None:
    """The vacuity guard: name the block, and count what it does.

    One copy and three captures around two runs. If a later revision changes
    that shape it should change this number deliberately, in the same commit.
    Counting also pins each command to its own line, which is what makes them
    visible to a line-oriented reader at all.
    """
    commands = remote_commands(the_provisioning_sequence())
    assert sum(c.startswith("scp ") for c in commands) == 1
    assert sum(c.startswith("ssh ") for c in commands) == 5


def test_every_remote_command_authenticates_as_the_key_step_1a_installs() -> None:
    for block in runbook_blocks(DESIGN.read_text(encoding="utf-8")):
        assert unauthenticated(block) == []


def test_step_1a_still_installs_the_key_the_sequence_names() -> None:
    """The two ends of one fact, held together.

    The sequence is authenticated only if the path it names is the path the
    owner was told to generate. Nothing else here reads step 1a, so without
    this the constant above could go on describing a key step 1a stopped
    installing, and every other test would still pass.
    """
    step_1a = _STEP_1A.search(DESIGN.read_text(encoding="utf-8"))
    assert step_1a is not None, "DESIGN.md section 19 step 1a was not found"
    assert KEY in step_1a.group()


def test_an_identity_assigned_below_its_uses_authenticates_nothing() -> None:
    """Control: the shipped block, resolving through a variable set too late.

    Every command then expands an unset variable, which is `ssh` with no
    identity at all. The second half is what makes this a measurement of
    *order*: the same commands and the same assignment, in the other order,
    must pass. Without it, a checker that had simply stopped resolving
    variables would satisfy the first half.
    """
    body = [
        line
        for line in _rewrite(
            the_provisioning_sequence(), lambda c: _with_identity(c, "$key")
        ).splitlines()
        if not _ASSIGNMENT.match(line)
    ]
    assignment = f"key={KEY}"
    _assert_nothing_authenticates("\n".join(body + [assignment]))
    assert unauthenticated("\n".join([assignment] + body)) == [], (
        "the same block with the same assignment above its uses must authenticate,"
        " or this control is measuring something other than order"
    )


def test_an_identity_that_is_not_step_1as_authenticates_nothing() -> None:
    """Control: same basename, different file — `/tmp/networth-vps.key`."""
    _assert_nothing_authenticates(
        _rewrite(the_provisioning_sequence(), lambda c: _with_identity(c, "/tmp/networth-vps.key"))
    )


def test_dropping_identities_only_from_one_command_is_caught() -> None:
    """Control: one command left able to authenticate as some other loaded key.

    Asserting *which* command fails is the difference between a predicate that
    reads every command and one that fails the block wholesale on any defect.
    """
    weakened = _rewrite(the_provisioning_sequence(), _without_identities_only, limit=1)
    assert [command.split()[0] for command in unauthenticated(weakened)] == ["scp"]


def test_the_sequence_as_rev_21_shipped_it_authenticates_nothing() -> None:
    """Control: the original defect, reconstructed out of its own fix."""
    _assert_nothing_authenticates(
        _rewrite(
            the_provisioning_sequence(),
            lambda command: _without_identities_only(_with_identity(command, None)),
        )
    )
