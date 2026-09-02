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
- that key is the **only** credential the command can offer, counting both
  spellings of an identity and refusing anything this module cannot read;
- and the command is pinned to it by the `IdentitiesOnly` value OpenSSH would
  *use*, without which a loaded agent can authenticate a command whose `-i` is
  wrong, which is what makes the clauses above mean anything at all.

The last two clauses are here because round 2 asked for *tokens* instead of the
effective policy, and two edits that keep every token went through. Both were
measured on `zelengs-macbook-air-2` with `ssh -G`, OpenSSH 10.2, rather than
reasoned about:

- command-line settings are **first-value-wins**, so
  `-o IdentitiesOnly=no -o IdentitiesOnly=yes` reports `identitiesonly no`.
  A predicate asking whether `=yes` is *present* says "pinned" about a command
  that is not;
- identities **accumulate** and have two spellings: `ssh -G -i A
  -o IdentityFile=B` reports both, as does `-F` naming a file that sets
  `IdentityFile`. Reading only the first `-i` reports A while B can
  authenticate.

Round 3 returned the same sentence one word further along: the module asked
*which* tokens a command carried without asking *where* they sat. Option
parsing stops at a position, and `ssh` and `scp` stop in different places —
measured here with `ssh -G` and a local `scp`, not read off a manual page:

- **`ssh` resumes after its destination** and stops at the remote command.
  `ssh -G host -i A -o IdentitiesOnly=yes` reports both, and `-i` before the
  host and `-i` after it accumulate; the same two options moved behind a
  command token report `identitiesonly no` and the default identity list;
- **`scp` stops at its first path operand.** `scp a -i k dst/` copies a file
  literally named `-i`.

So the shipped options moved behind `'bash /root/host-state.sh'` — every token
still present, in the same command, in the same order relative to each other —
leave `ssh` authenticating as whatever an agent offers, and a predicate reading
the flat token list called that pinned. `_option_tokens` is where the boundary
now lives, and it is the reason the two controls anchored on the shipped text
give **opposite verdicts for the two programs** from one edit.

This is a shape check, with the usual limits: it cannot prove the key is on the
host, which only the host can answer, and it reads a block as a flat sequence,
so an assignment nested inside its own subshell would count as in scope here
while the real shell discarded it. It also says nothing about *which* host a
command reaches — the operand region is where destinations live, and stating
that limit is cheaper than implying a guarantee that was never written. The
case it buys is the one that happened — a remote command added or edited
without a usable identity.

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

#: Normalised the way `_setting` normalises, so that `-o IdentitiesOnly=yes`,
#: `-oIdentitiesOnly=yes` and `-o "IdentitiesOnly yes"` — one instruction to
#: `ssh`, three spellings — are one setting here too. `ssh` reads option names
#: case-insensitively (`-o identitiesonly=yes` is accepted), and reports the
#: value lowercased (`-o IdentitiesOnly=YES` reports `yes`).
IDENTITIES_ONLY = "identitiesonly"

#: The value of that setting which pins the command to its own `-i`.
PINNED = "yes"

#: An identity, spelled the long way. `-i path` and `-o IdentityFile=path` are
#: the same instruction, and they accumulate rather than replace.
IDENTITY_FILE = "identityfile"

#: The only option spellings this module reads: `-i x`, `-ix`, `-o x`, `-ox`.
#: Anything else that looks like an option is refused — see `unreadable`.
KNOWN_FLAGS = ("-i", "-o")

#: The only `-o` setting names it reads.
KNOWN_SETTINGS = frozenset({IDENTITIES_ONLY, IDENTITY_FILE})

#: How many positional operands option parsing survives, per program — where
#: each program stops reading options at all. Measured on this machine, because
#: the obvious model is wrong for one of the two: `ssh` does **not** stop at its
#: first operand, it resumes after the destination and stops at the remote
#: command (`ssh -G host -i A -o IdentitiesOnly=yes` reports both; behind a
#: command token it reports neither). `scp` does stop at the first operand
#: (`scp a -i k dst/` reports `cp: -i: No such file or directory`).
OPTIONS_SURVIVE = {"ssh": 1, "scp": 0}


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


def _option_tokens(command: str) -> list[str]:
    """The tokens this command's program reads as options — and no others.

    Everything below asks what a command's options *say*; this is the one place
    that decides which tokens are options at all, and getting that wrong was
    round 3. Scanning the whole token list counts `-i` and `-o` that sit past
    the boundary, where `ssh` hands them to the remote shell and `scp` treats
    them as paths. The shipped block with its two options moved behind
    `'bash /root/host-state.sh'` keeps every token a flat scan looks for and
    authenticates with whatever the agent has.

    Operands are counted rather than stopped at, because `ssh` resumes: the
    destination is an operand that option parsing survives, and the remote
    command is the one it does not. `OPTIONS_SURVIVE` is that number, measured
    per program.

    An option-looking token this module does not know is taken to consume no
    argument, which can end the region early and hide real options behind it.
    That is safe in the only direction that matters: `unreadable` reports the
    unknown token itself, so such a command fails before the hidden options
    could have excused it. The reverse — modelling an unknown flag as taking an
    argument — would swallow a real operand and read the remote command as
    options.
    """
    tokens = shlex.split(command)
    survives = OPTIONS_SURVIVE[tokens[0]]
    options: list[str] = []
    operands = 0
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith("-"):
            operands += 1
            if operands > survives:
                break
            index += 1
            continue
        width = 2 if token in KNOWN_FLAGS else 1
        options += tokens[index : index + width]
        index += width
    return options


def _setting(option: str) -> tuple[str, str]:
    """One `-o` argument as `(name, value)`, the way `ssh` reads it.

    The name is lowered because `ssh` matches it case-insensitively. The value
    is **not**: it can be a path, and `~/A.key` and `~/a.key` are two files.
    Lowering the whole string is what the previous version did, which was
    harmless only for as long as no value was a path.
    """
    name, _, value = re.sub(r"\s*=\s*|\s+", "=", option.strip()).partition("=")
    return name.lower(), value


def _settings(tokens: list[str]) -> list[tuple[str, str]]:
    """Every `-o` setting in an option region, in the order `ssh` reads them.

    Takes the region rather than the command: a `-o` past the option boundary
    is not a setting at all, and every caller here gets its tokens from
    `_option_tokens`.

    A list, not a set. Order *is* the question for `IdentitiesOnly`: the set
    this used to return held `no` and `yes` at once and still answered "yes,
    it is pinned" about a command OpenSSH pins to nothing.
    """
    settings = []
    for index, token in enumerate(tokens):
        if token == "-o" and index + 1 < len(tokens):
            settings.append(_setting(tokens[index + 1]))
        elif token.startswith("-o") and token != "-o":
            settings.append(_setting(token[2:]))
    return settings


def _resolve(argument: str, variables: dict[str, str]) -> str | None:
    """One path as written, with `$name` / `${name}` expanded — or `None`.

    `None` is a name no assignment above this command has set: in the real
    shell it expands to nothing, so it must never compare equal to `KEY`.
    """
    if reference := _REFERENCE.match(argument):
        return variables.get(reference.group(1))
    return argument


def identities_of(command: str, variables: dict[str, str]) -> list[str | None]:
    """Every identity one remote command offers, both spellings, resolved.

    `-i path` and `-o IdentityFile=path` are one instruction to `ssh` and they
    accumulate: `ssh -G -i A -o IdentityFile=B` reports both. Returning only
    the first `-i` — which is what this did — described A while B could
    authenticate just as well.

    An empty list is a command that offers nothing, which is the original
    defect; it fails the caller's test for the same reason a wrong path does —
    and it is also what a command whose `-i` sits past the option boundary
    offers, which is the round-3 mutation.
    """
    tokens = _option_tokens(command)
    identities: list[str | None] = []
    for index, token in enumerate(tokens):
        if token == "-i":
            argument = tokens[index + 1] if index + 1 < len(tokens) else None
            identities.append(_resolve(argument, variables) if argument is not None else None)
        elif token.startswith("-i") and len(token) > 2:
            identities.append(_resolve(token[2:], variables))
    identities += [
        _resolve(value, variables) for name, value in _settings(tokens) if name == IDENTITY_FILE
    ]
    return identities


def identities_only(command: str) -> str | None:
    """The `IdentitiesOnly` value OpenSSH would *use*, or `None` if unset.

    First value wins among command-line settings — measured here, not assumed:
    `ssh -G -o IdentitiesOnly=no -o IdentitiesOnly=yes` reports `no`, and the
    same pair in the other order reports `yes`. So asking whether the command
    *contains* `=yes` answers a different question than "is it pinned", and
    the two disagree exactly when someone has put a `no` in front.
    """
    for name, value in _settings(_option_tokens(command)):
        if name == IDENTITIES_ONLY:
            return value.lower()
    return None


def unreadable(command: str) -> list[str]:
    """Everything on this command this module cannot read, and so will not vouch for.

    Fail **closed**, and that is the lesson of the round rather than a detail
    of it. Round 2 asked "does it carry the tokens I like". The first draft of
    round 3 asked "does it carry one of the tokens I know are dangerous" — and
    that list was already incomplete the moment it was written. Measured here
    with `ssh -G`, all of these widen what may authenticate and none is an
    `-i`: `-o IdentityFile=k`, `-F file`, `-Ffile`, `-4F file`, `-4i k`, and
    `-o PreferredAuthentications=password`, which leaves keys out of it
    entirely. Enumerating the ways to widen a command is the losing side of
    that exchange; enumerating what this module actually understands is finite,
    short, and is the whole of what it claims.

    So an option-looking token must be a spelling of `-i` or `-o`, and an `-o`
    setting must be one of the two names read above. A sequence that grows a
    new flag fails until this module is taught it — one reviewed line, against
    the alternative of a token nothing read at all.

    Scoped to the option region, like everything else here. An option-looking
    token past the boundary is a remote-command argument or a path, and
    reddening the block for one would be a false alarm of exactly the kind the
    green controls exist to prevent.
    """
    tokens = _option_tokens(command)
    unread = [name for name, _ in _settings(tokens) if name not in KNOWN_SETTINGS]
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in KNOWN_FLAGS:
            index += 2  # the flag, and the argument it takes from the next token
            continue
        if token.startswith("-") and not token.startswith(KNOWN_FLAGS):
            unread.append(token)
        index += 1
    return unread


def unauthenticated(block: str) -> list[str]:
    """Commands in `block` that could reach the host as anything but step 1a's key.

    Three questions, and a command has to answer all three: the only identity
    it can offer is step 1a's key; it is pinned to it by the setting OpenSSH
    would actually use, so a loaded agent cannot substitute; and it carries
    nothing whose effect on that this module cannot read.

    Comparing the identities as a **set** is deliberate. The property is "the
    only key this can offer is step 1a's", and naming the same key twice does
    not widen it — `ssh -G -i A -i A` reports one `A`. Naming a second, and
    naming none, both fail here.
    """
    return [
        command
        for command, variables in _resolved(block)
        if set(identities_of(command, variables)) != {KEY}
        or identities_only(command) != PINNED
        or unreadable(command)
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
        if token == "-o" and _setting(following)[0] == IDENTITIES_ONLY:
            index += 2
        elif token.startswith("-o") and token != "-o" and _setting(token[2:])[0] == IDENTITIES_ONLY:
            index += 1
        else:
            kept.append(token)
            index += 1
    assert len(kept) < len(tokens), f"nothing to drop, this command does not set it: {command}"
    return shlex.join(kept)


def _carrying(*extra: str) -> Callable[[str], str]:
    """An edit that inserts `extra` immediately after the command name.

    First position, because that is where a setting has to be to win: these
    build the two mutations round 2 passed, and the redundant-but-effective
    spellings that must keep passing, out of the same one-line helper.
    """

    def edit(command: str) -> str:
        tokens = shlex.split(command)
        return shlex.join(tokens[:1] + list(extra) + tokens[1:])

    return edit


#: Two markers out of the shipped block's own text, used to place the positional
#: controls below. The destination is in every one of the six commands; the
#: remote command is in the five `ssh` ones and in no `scp`, which is what lets
#: one edit ask the two programs different questions.
DESTINATION = "root@"
REMOTE_COMMAND = "bash /root/"


def _split_options(command: str) -> tuple[list[str], list[str]]:
    """A command's `-i`/`-o` tokens and everything else, ignoring position.

    Deliberately **not** `_option_tokens`. A control that asked the parser under
    test where the option boundary is would move the options to wherever that
    parser believed it was, and then agree with itself whatever it believed —
    an edit that cannot fail. This one knows only that `-i` and `-o` take an
    argument; where the result lands is then decided by a marker out of the
    block's own text, and what the two programs do with tokens there was
    measured separately.
    """
    tokens = shlex.split(command)
    options: list[str] = []
    rest: list[str] = []
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token in KNOWN_FLAGS:
            options += tokens[index : index + 2]
            index += 2
        elif token.startswith(KNOWN_FLAGS):
            options.append(token)
            index += 1
        else:
            rest.append(token)
            index += 1
    return options, rest


def _moved_behind(marker: str) -> Callable[[str], str]:
    """An edit that moves a command's `-i`/`-o` options behind `marker`'s token.

    Not an insertion: the options that were there are the ones that move, so
    every token the shipped command had is still present and only its position
    changed — which is the whole of what round 3 found. A command with no such
    token is returned unmodified, so a marker only the `ssh` lines carry leaves
    the `scp` line alone and the caller can assert *which* commands fail.
    """

    def edit(command: str) -> str:
        tokens = shlex.split(command)
        options, rest = _split_options(command)
        assert options, f"nothing to move, this command names no options: {command}"
        positions = [index for index, token in enumerate(rest) if marker in token]
        if not positions:
            return command
        at = positions[0]
        moved = tokens[:1] + rest[: at + 1] + options + rest[at + 1 :]
        assert len(moved) == len(tokens), f"the edit changed the token count: {command}"
        assert moved.index(options[0]) > moved.index(rest[at]), "the options did not move"
        return shlex.join(moved)

    return edit


def _inserted_behind(marker: str, *extra: str) -> Callable[[str], str]:
    """An edit that adds `extra` immediately behind `marker`'s token."""

    def edit(command: str) -> str:
        tokens = shlex.split(command)
        positions = [index for index, token in enumerate(tokens) if marker in token]
        if not positions:
            return command
        at = positions[0] + 1
        return shlex.join(tokens[:at] + list(extra) + tokens[at:])

    return edit


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


def test_a_leading_identities_only_no_authenticates_nothing() -> None:
    """Control: round 2's first escape — `=no` in front of the shipped `=yes`.

    Every token the shipped block had is still there, including the `=yes`
    this used to look for. OpenSSH 10.2 on `zelengs-macbook-air-2` reports the
    effective setting as `no`, so a loaded agent may authenticate these
    commands: the block is unpinned and the old predicate called it pinned.

    The second form carries the `no` and a `yes` behind it, which is the exact
    mirror of the pair `test_spellings_that_still_authenticate_are_not_rejected`
    requires to pass. Same two settings, same place, opposite order, opposite
    verdict — that pair is the whole claim that precedence is modelled here.
    """
    for extra in (
        ("-o", "IdentitiesOnly=no"),
        ("-o", "IdentitiesOnly=no", "-o", "IdentitiesOnly=yes"),
    ):
        _assert_nothing_authenticates(_rewrite(the_provisioning_sequence(), _carrying(*extra)))


def test_a_second_identity_authenticates_nothing() -> None:
    """Control: round 2's second escape — another `-i` beside the canonical one.

    `ssh` keeps both, so the sequence no longer shows that step 1a's key is
    what authenticated. Reading only the first `-i` reported that it was.
    """
    _assert_nothing_authenticates(
        _rewrite(the_provisioning_sequence(), _carrying("-i", "~/.ssh/id_ed25519"))
    )


def test_a_second_identity_spelled_as_an_option_authenticates_nothing() -> None:
    """Control: the same widening through `-o IdentityFile=`, which `-i` misses.

    Not one of the two mutations that came back, and it defeated the round-2
    predicate the same way: identities were read from `-i` only, so this one
    was invisible to it rather than merely mis-ordered.
    """
    _assert_nothing_authenticates(
        _rewrite(the_provisioning_sequence(), _carrying("-o", "IdentityFile=~/.ssh/id_ed25519"))
    )


def test_anything_this_module_cannot_read_authenticates_nothing() -> None:
    """Control: the fail-closed rule, one addition at a time.

    Each of these was measured with `ssh -G` on this machine and each widens
    what may authenticate past step 1a's key, by a route that is not an `-i`:
    a config file nothing reviewed, in three spellings including one bundled
    with another flag; an identity bundled the same way; credentials from an
    agent, a token or a certificate; and an authentication method that skips
    keys altogether. **None of them is named in the module.** They are refused
    because they are not among the four spellings it reads, which is the point
    — the list below is a sample of an open set, and the guard does not depend
    on it being complete.

    One at a time, so this cannot pass because some other entry did the work.
    """
    additions = [
        ("-F", "/tmp/ssh.conf"),
        ("-F/tmp/ssh.conf",),
        ("-4F", "/tmp/ssh.conf"),
        ("-4i", "~/.ssh/id_ed25519"),
        ("-o", "Include=/tmp/ssh.conf"),
        ("-o", "CertificateFile=/tmp/id.pub"),
        ("-o", "IdentityAgent=/tmp/agent.sock"),
        ("-o", "PKCS11Provider=/tmp/p11.so"),
        ("-o", "SecurityKeyProvider=/tmp/sk.so"),
        ("-o", "PreferredAuthentications=password"),
        ("-o", "PasswordAuthentication=yes"),
    ]
    for addition in additions:
        mutated = _rewrite(the_provisioning_sequence(), _carrying(*addition))
        _assert_nothing_authenticates(mutated)


def test_options_behind_the_ssh_remote_command_authenticate_nothing() -> None:
    """Control: round 3's escape — the shipped options, moved, nothing added.

    `ssh root@… 'bash /root/host-state.sh' -i "$vps_key" -o IdentitiesOnly=yes`
    carries every token the round-2 predicate looked for, in the same relative
    order, and it authenticates with whatever the agent holds: measured on this
    machine, `ssh -G host echo -i /etc/hosts -o IdentitiesOnly=yes` reports
    `identitiesonly no` and the five default identity files. Past the remote
    command those tokens are arguments to the remote shell.

    The `scp` has no such token and is deliberately left untouched, so this
    asserts *which* five commands broke rather than that the block did.
    """
    mutated = _rewrite(the_provisioning_sequence(), _moved_behind(REMOTE_COMMAND))
    assert [command.split()[0] for command in unauthenticated(mutated)] == ["ssh"] * 5


def test_options_behind_the_destination_split_the_two_programs() -> None:
    """Control: one edit, opposite verdicts — because the programs differ.

    This is the pair that says the boundary is modelled per program rather than
    guessed at. The same move, behind the destination that all six commands
    name:

    - `scp` stops reading options at its first path operand, so the `scp` line's
      `-i` and `-o` become paths — `scp a -i k dst/` reports
      `cp: -i: No such file or directory` here. It must go **red**;
    - `ssh` resumes reading options after its destination, so the five `ssh`
      lines are pinned exactly as shipped — `ssh -G host -i A
      -o IdentitiesOnly=yes` reports both. They must stay **green**.

    Getting this half wrong is not a fail-open, it is worse for the guard's
    life expectancy: a rule that stopped at the first operand for both programs
    would redden five commands that authenticate correctly, and the next
    author's cheapest fix is to delete the guard.
    """
    mutated = _rewrite(the_provisioning_sequence(), _moved_behind(DESTINATION))
    assert [command.split()[0] for command in unauthenticated(mutated)] == ["scp"]


def test_a_second_identity_after_the_ssh_destination_authenticates_nothing() -> None:
    """Control: the fail-open direction of the region the fix newly accepts.

    Accepting options behind the destination is new acceptance surface, and new
    acceptance surface needs a red control or it is only an assumption. `ssh -G
    -i A host -i B` reports **both** identities, so a key added after the host
    widens what may authenticate exactly as one added before it, and the five
    `ssh` lines must fail.

    On the `scp` line the same insertion lands past that program's boundary,
    where it is a path rather than a credential, so it stays green — the guard
    claims authentication, not that a command works.
    """
    mutated = _rewrite(
        the_provisioning_sequence(), _inserted_behind(DESTINATION, "-i", "~/.ssh/id_ed25519")
    )
    assert [command.split()[0] for command in unauthenticated(mutated)] == ["ssh"] * 5


def test_spellings_that_still_authenticate_are_not_rejected() -> None:
    """The other half of the two clauses above: what must stay *green*.

    A guard that answers "no" to everything is as useless as one that answers
    "yes", and it is worse than useless when the "no" lands on a sequence that
    works — the next author's cheapest fix is then to weaken the guard. Each
    of these is a command OpenSSH treats exactly as the shipped one, and each
    is rejected by the stricter rule of counting occurrences rather than
    modelling them:

    - the identity spelled as `-o IdentityFile=`, which is what `-i` means;
    - that identity named twice, which `ssh -G -i A -i A` collapses to one;
    - a redundant second `IdentitiesOnly=yes`, still effectively `yes`;
    - and a `=yes` with a `=no` *behind* it, which first-value-wins makes
      `yes`. This one is the sharpest: it is the same two settings in the same
      position as the second mutation in
      `test_a_leading_identities_only_no_authenticates_nothing`, in the other
      order. Passing here and failing there is precisely the claim that
      precedence is modelled rather than occurrences counted — and a rule of
      "exactly one `IdentitiesOnly` setting" would fail this one.
    """
    sequence = the_provisioning_sequence()
    respellings = {
        "identity as an option": lambda c: _carrying("-o", "IdentityFile=$vps_key")(
            _with_identity(c, None)
        ),
        "the same identity twice": _carrying("-i", "$vps_key"),
        "a redundant identities-only": _carrying("-o", "IdentitiesOnly=yes"),
        "an overridden identities-only": _carrying(
            "-o", "IdentitiesOnly=yes", "-o", "IdentitiesOnly=no"
        ),
    }
    for description, edit in respellings.items():
        rewritten = _rewrite(sequence, edit)
        assert len(remote_commands(rewritten)) == len(remote_commands(sequence))
        assert unauthenticated(rewritten) == [], (
            f"{description}: this authenticates as step 1a's key exactly as the shipped"
            " block does, and a guard that fails it teaches the next author to delete it"
        )


def test_the_sequence_as_rev_21_shipped_it_authenticates_nothing() -> None:
    """Control: the original defect, reconstructed out of its own fix."""
    _assert_nothing_authenticates(
        _rewrite(
            the_provisioning_sequence(),
            lambda command: _without_identities_only(_with_identity(command, None)),
        )
    )
