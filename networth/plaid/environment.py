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

from networth.config import SECRETS_DIR, ConfigError, read_env_file

DATA_DIR_NAME = "networth-data"
ENV_VAR = "NETWORTH_ENV"

__all__ = [
    "DATA_DIR_NAME",
    "ENV_VAR",
    "SECRETS_DIR",
    "ConfigError",
    "Paths",
    "PlaidCredentials",
    "PlaidEnvironment",
    "load_credentials",
    "paths_for",
    "selected_environment",
]


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

    **Both fields are credentials and neither is rendered.** The default
    dataclass repr would print them into any log line or traceback that touched
    this object.

    ``client_id`` is redacted too, which the first version of this class got
    wrong by treating it as an identifier (found in review, round 1). Plaid
    authenticates a request with the *pair* — `clientId` and `secret` travel
    together in every call this program makes — so half the pair in a log is
    half of a working credential, and it is the half that identifies which
    account the other half unlocks. The environment is not a credential and
    stays, because it is the field that makes a stray repr diagnosable at all.
    """

    client_id: str
    secret: str
    environment: PlaidEnvironment

    def __repr__(self) -> str:
        return (
            "PlaidCredentials(client_id=<redacted>, "
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
        # Also not echoed, for the same reason and one more: this value reaches
        # us from the process environment, where a mis-pasted unit file or an
        # exported shell variable can put anything at all, and the refusal is
        # logged. Naming the two acceptable values is the diagnostic that
        # matters; the operator can read back what he set.
        raise ConfigError(
            f"{ENV_VAR} is set to something that is not an environment; expected "
            f"{PlaidEnvironment.SANDBOX.value!r} or {PlaidEnvironment.PRODUCTION.value!r} "
            f"(the value is not echoed here, because it is logged)"
        ) from None


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
    values = read_env_file(path, describe=f"Plaid credentials for {environment.value!r}")
    declared = values.get("PLAID_ENV")
    if declared != environment.value:
        # The value is NOT echoed. An earlier version did, reasoning that
        # PLAID_ENV is a closed vocabulary — but the vocabulary is what a
        # correct file contains, and this branch runs precisely when the file is
        # not correct. Whatever is on that line is arbitrary text from the file
        # that also holds the Plaid master credential, and the most likely way
        # this branch ever fires on a real host is an editing accident in that
        # file. The path and the expectation are enough to fix it in one step,
        # and neither is a secret. (Found in review, round 1.)
        raise ConfigError(
            f"{path} declares a PLAID_ENV that is not {environment.value!r}, which is what "
            f"{ENV_VAR} selected; refusing to start (section 15). The value is not shown "
            f"here because this file holds the Plaid credential — read the line itself"
        )

    missing = [key for key in ("PLAID_CLIENT_ID", "PLAID_SECRET") if not values.get(key)]
    if missing:
        raise ConfigError(f"{path} has no usable {', '.join(missing)}")

    return PlaidCredentials(
        client_id=values["PLAID_CLIENT_ID"],
        secret=values["PLAID_SECRET"],
        environment=environment,
    )
