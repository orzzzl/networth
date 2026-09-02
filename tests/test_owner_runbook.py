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
the host names the key `DESIGN.md` §19 step 1a installs.** That is a shape
check, with the usual limit — it cannot prove the key is on the host, which only
the host can answer — and the case it buys is the one that happened: a remote
command added or edited without one.

`test_the_checks_can_fail` is not decoration. Every assertion below is "no
command is missing an identity", which is also what an empty list of commands
says, so a block this module fails to find would pass it silently.
"""

from __future__ import annotations

import re
import shlex

from tests.conftest import REPO_ROOT

DESIGN = REPO_ROOT / "DESIGN.md"

#: The body of a fenced block, fence to fence. The fences are allowed to be
#: indented because §19's sequence sits inside a numbered step and is — the
#: first version of this module anchored on column 0 and found nothing.
_FENCED = re.compile(r"^[ \t]*```[^\n]*\n(?P<body>.*?)^[ \t]*```", re.MULTILINE | re.DOTALL)

#: A line that opens a connection to a host. Anchored, so `git … ssh` in the
#: middle of a line is not one and a prose mention is never reached at all.
_REMOTE = re.compile(r"^(ssh|scp)\s")

#: The key §19 step 1a generates and the owner installs. Named by suffix: the
#: block reaches it through `~`, step 1a writes it out in full.
KEY = "networth-vps.key"


def runbook_blocks(document: str) -> list[str]:
    """Every fenced block that reaches a host — the ones the owner pastes."""
    return [m.group("body") for m in _FENCED.finditer(document) if "root@" in m.group("body")]


def remote_commands(block: str) -> list[str]:
    return [line.strip() for line in block.splitlines() if _REMOTE.match(line.strip())]


def _assignments(block: str) -> dict[str, str]:
    """`name=value` lines, so an identity passed as `"$name"` can be resolved."""
    found = {}
    for line in block.splitlines():
        if match := re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(\S+)\s*$", line):
            found[match.group(1)] = match.group(2)
    return found


def identity_of(command: str, block: str) -> str | None:
    """The `-i` argument of one remote command, with `"$name"` resolved.

    `None` means the command names no identity — which is the failure this
    module exists for, not an absence of information.
    """
    tokens = shlex.split(command)
    if "-i" not in tokens:
        return None
    argument = tokens[tokens.index("-i") + 1]
    if argument.startswith("$"):
        return _assignments(block).get(argument.lstrip("${").rstrip("}"))
    return argument


def unauthenticated(block: str) -> list[str]:
    """Commands in `block` that would reach the host with no key of their own."""
    return [
        command
        for command in remote_commands(block)
        if (identity := identity_of(command, block)) is None or not identity.endswith(KEY)
    ]


def test_the_provisioning_sequence_is_present_and_is_what_is_being_checked() -> None:
    """The vacuity guard: name the block, and count what it does.

    One copy and three captures around two runs. If a later revision changes
    that shape it should change this number deliberately, in the same commit.
    """
    document = DESIGN.read_text(encoding="utf-8")
    blocks = [b for b in runbook_blocks(document) if "provision-host.sh" in b]
    assert len(blocks) == 1, "DESIGN.md section 19 step 3.1's sequence was not found"

    commands = remote_commands(blocks[0])
    assert sum(c.startswith("scp ") for c in commands) == 1
    assert sum(c.startswith("ssh ") for c in commands) == 5


def test_every_remote_command_names_the_key_step_1a_installs() -> None:
    for block in runbook_blocks(DESIGN.read_text(encoding="utf-8")):
        assert unauthenticated(block) == []


def test_the_checks_can_fail() -> None:
    """A positive control: the same predicate, over the block as it shipped."""
    as_shipped = """
      vps_key=~/agents/secrets/networth-vps.key
      scp ~/networth-run/provision-host.sh root@100.102.245.37:/root/ &&
      ssh root@100.102.245.37 'bash /root/host-state.sh' >~/host-state-0.txt
    """
    assert len(remote_commands(as_shipped)) == 2
    assert len(unauthenticated(as_shipped)) == 2

    wrong_key = 'ssh -i ~/.ssh/id_ed25519 root@100.102.245.37 "id"'
    assert unauthenticated(wrong_key) == [wrong_key]
