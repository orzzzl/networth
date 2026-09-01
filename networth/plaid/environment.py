"""``NETWORTH_ENV`` selection, which fails closed.

``DESIGN.md`` section 15: the variable is **required with no default**, and it
selects the credential file, the items file and the database path *together*. A
Sandbox rehearsal must not be one edited constant away from writing into the
Production history, and the mismatch that would otherwise be silent — a Sandbox
credential in a file labelled production — has to be a startup failure rather
than a run whose data nobody questions.

Nothing here is secret: the values live in ``/etc/networth/`` on the sync host,
installed by the owner, and this module reads them at runtime. No default, no
fallback to the other host's directory, and no value from a credential file is
ever put in an exception message.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

SECRETS_DIR = Path("/etc/networth")
DATA_DIR_NAME = "networth-data"
ENV_VAR = "NETWORTH_ENV"


class ConfigError(RuntimeError):
    """The process must not start with the configuration it was given."""


class PlaidEnvironment(StrEnum):
    """The two environments, and the only two.

    Section 15 pairs each with its own credential file, items file and database.
    ``development`` is not here because this project never had one.
    """

    SANDBOX = "sandbox"
    PRODUCTION = "production"

    @property
    def api_host(self) -> str:
        """The Plaid API host for this environment.

        Kept beside the file selection rather than inside the client, so that
        "which credentials" and "which server" cannot be decided separately.
        That pairing is the whole point of section 15's fail-closed rule.
        """
        if self is PlaidEnvironment.SANDBOX:
            return "https://sandbox.plaid.com"
        return "https://production.plaid.com"


@dataclass(frozen=True, slots=True)
class Paths:
    """Every path the selected environment implies. Chosen together, always."""

    credentials: Path
    items: Path
    database: Path


def paths_for(
    environment: PlaidEnvironment,
    *,
    secrets_dir: Path = SECRETS_DIR,
    data_dir: Path | None = None,
) -> Paths:
    """The three paths section 15 pairs with ``environment``.

    ``secrets_dir`` and ``data_dir`` exist so tests can point at a temporary
    directory. They are **not** a way to read another host's secrets: each host
    reads only its own directory and never falls back to the other's, because
    that fallback is how a path bug becomes "it worked on my machine" for a file
    holding access tokens (section 15, ``AGENTS.md`` rule 1).
    """
    root = data_dir if data_dir is not None else Path.home() / DATA_DIR_NAME
    if environment is PlaidEnvironment.SANDBOX:
        return Paths(
            credentials=secrets_dir / "plaid-sandbox.env",
            items=secrets_dir / "plaid-items-sandbox.json",
            database=root / "networth-sandbox.db",
        )
    return Paths(
        credentials=secrets_dir / "plaid.env",
        items=secrets_dir / "plaid-items.json",
        database=root / "networth.db",
    )


@dataclass(frozen=True, slots=True)
class PlaidCredentials:
    """One environment's Plaid credentials, read from its own file.

    ``secret`` is a credential. It is not rendered: the default dataclass repr
    would print it into any log line or traceback that touched this object.
    """

    client_id: str
    secret: str
    environment: PlaidEnvironment

    def __repr__(self) -> str:
        return (
            f"PlaidCredentials(client_id={self.client_id!r}, "
            f"secret=<redacted>, environment={self.environment.value!r})"
        )


def selected_environment(env: dict[str, str] | None = None) -> PlaidEnvironment:
    """Read ``NETWORTH_ENV``. Absent or unrecognised is a refusal, not a default.

    Defaulting this would mean a forgotten variable silently picks an
    environment — and the direction it would pick wrong is the expensive one,
    because Production is where the ten lifetime Items live (F2).
    """
    source = os.environ if env is None else env
    raw = source.get(ENV_VAR)
    if raw is None or raw == "":
        raise ConfigError(
            f"{ENV_VAR} is required and has no default; set it to "
            f"{PlaidEnvironment.SANDBOX.value!r} or {PlaidEnvironment.PRODUCTION.value!r}"
        )
    try:
        return PlaidEnvironment(raw)
    except ValueError:
        raise ConfigError(
            f"{ENV_VAR}={raw!r} is not an environment; expected "
            f"{PlaidEnvironment.SANDBOX.value!r} or {PlaidEnvironment.PRODUCTION.value!r}"
        ) from None


def _parse_env_file(text: str, path: Path) -> dict[str, str]:
    """``KEY=value`` lines, the shape systemd's ``EnvironmentFile`` reads.

    No shell: this file holds the Plaid master credential, and running it would
    make one stray character on the owner's host an arbitrary command. Values
    are taken literally, minus one layer of surrounding quotes.

    No value ever appears in an error raised here — a parse failure is not a
    reason to print a secret (the same rule ``TokenStore`` follows).
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


def load_credentials(
    environment: PlaidEnvironment,
    *,
    secrets_dir: Path = SECRETS_DIR,
) -> PlaidCredentials:
    """Load the credential file this environment selects, and check it agrees.

    The assertion is section 15's fail-closed rule: the file's own ``PLAID_ENV``
    must equal the environment that chose it. A Production secret sitting in
    ``plaid-sandbox.env`` is caught here rather than at the moment it spends a
    lifetime Item slot.
    """
    path = paths_for(environment, secrets_dir=secrets_dir).credentials
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(
            f"no Plaid credentials for {environment.value!r}: {path} does not exist. "
            f"The owner installs it; agents never write it (AGENTS.md rule 3)"
        ) from None
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror}") from None

    values = _parse_env_file(text, path)
    declared = values.get("PLAID_ENV")
    if declared != environment.value:
        # The declared value is a closed vocabulary, not a secret, so naming it
        # is what makes this failure fixable in one step.
        raise ConfigError(
            f"{path} declares PLAID_ENV={declared!r} but {ENV_VAR} selected "
            f"{environment.value!r}; refusing to start (section 15)"
        )

    missing = [key for key in ("PLAID_CLIENT_ID", "PLAID_SECRET") if not values.get(key)]
    if missing:
        raise ConfigError(f"{path} has no usable {', '.join(missing)}")

    return PlaidCredentials(
        client_id=values["PLAID_CLIENT_ID"],
        secret=values["PLAID_SECRET"],
        environment=environment,
    )
