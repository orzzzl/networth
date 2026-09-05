"""Reading the owner-installed files in ``/etc/networth/``.

``DESIGN.md`` section 15 puts every daemon runtime secret in one directory on
the sync host, in the ``KEY=value`` shape systemd's ``EnvironmentFile`` reads.
Two of those files are credentials for different services — Plaid's pair and the
quotes key — so the parser that opens them lives here rather than inside either
client. One implementation means one place where the two rules that matter are
enforced: **no shell**, and **no value ever appears in an error**.

Nothing in this module is itself secret. It reads files the owner installs; it
never writes one, and it never falls back to another host's directory
(``AGENTS.md`` rule 1).
"""

from __future__ import annotations

from pathlib import Path

SECRETS_DIR = Path("/etc/networth")


class ConfigError(RuntimeError):
    """The process must not start with the configuration it was given."""


def parse_env_file(text: str, path: Path) -> dict[str, str]:
    """``KEY=value`` lines, the shape systemd's ``EnvironmentFile`` reads.

    No shell: these files hold long-lived credentials, and running one would
    make a stray character on the owner's host an arbitrary command. Values are
    taken literally, minus one layer of surrounding quotes.

    No value ever appears in an error raised here — a parse failure is not a
    reason to print a secret (the same rule ``TokenStore`` follows). The path
    and the line number are enough to fix the file in one step, and neither is
    a secret.
    """
    values: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].lstrip()
        key, separator, value = stripped.partition("=")
        if not separator:
            raise ConfigError(f"{path}:{number} is not a KEY=value line")
        key = key.strip()
        if not key:
            raise ConfigError(f"{path}:{number} has an empty key")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def read_env_file(path: Path, *, describe: str) -> dict[str, str]:
    """Open one of the owner's files and parse it, or refuse with the reason.

    ``describe`` names what the file is for, so a missing-file message can say
    which capability is unconfigured without naming a key inside it. The
    "the owner installs it" half of the message is deliberate: an agent that
    reads this error must not resolve it by writing the file itself
    (``AGENTS.md`` rule 3).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(
            f"no {describe}: {path} does not exist. "
            f"The owner installs it; agents never write it (AGENTS.md rule 3)"
        ) from None
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror}") from None
    return parse_env_file(text, path)
