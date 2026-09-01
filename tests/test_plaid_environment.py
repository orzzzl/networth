"""Section 15's fail-closed selection. The expensive direction is Production, so
every test here is about refusing rather than about succeeding."""

from __future__ import annotations

from pathlib import Path

import pytest

from networth.plaid.environment import (
    ConfigError,
    PlaidEnvironment,
    load_credentials,
    paths_for,
    selected_environment,
)

SANDBOX_FILE = (
    "PLAID_CLIENT_ID=synthetic-client\nPLAID_SECRET=synthetic-sandbox-secret\nPLAID_ENV=sandbox\n"
)


def write_credentials(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def test_networth_env_is_required() -> None:
    with pytest.raises(ConfigError) as raised:
        selected_environment({})
    assert "NETWORTH_ENV" in str(raised.value)


def test_empty_networth_env_is_not_a_default() -> None:
    with pytest.raises(ConfigError):
        selected_environment({"NETWORTH_ENV": ""})


def test_unrecognised_networth_env_is_refused() -> None:
    """`development` is a real Plaid environment and not one of ours. Accepting
    it would select no files at all."""
    with pytest.raises(ConfigError):
        selected_environment({"NETWORTH_ENV": "development"})


@pytest.mark.parametrize("value", ["sandbox", "production"])
def test_both_environments_parse(value: str) -> None:
    assert selected_environment({"NETWORTH_ENV": value}).value == value


def test_the_three_paths_move_together(tmp_path: Path) -> None:
    """The property section 15 is actually buying: no pair of environments
    shares any of the three, so a Sandbox rehearsal physically cannot write into
    the Production history."""
    sandbox = paths_for(PlaidEnvironment.SANDBOX, secrets_dir=tmp_path, data_dir=tmp_path)
    production = paths_for(PlaidEnvironment.PRODUCTION, secrets_dir=tmp_path, data_dir=tmp_path)
    assert sandbox.credentials != production.credentials
    assert sandbox.items != production.items
    assert sandbox.database != production.database
    assert len({sandbox.database, production.database}) == 2


def test_default_paths_are_the_documented_ones() -> None:
    """The literal locations DESIGN.md section 15 names, asserted rather than
    described — the owner installs these files by hand at these paths."""
    production = paths_for(PlaidEnvironment.PRODUCTION)
    sandbox = paths_for(PlaidEnvironment.SANDBOX)
    assert production.credentials == Path("/etc/networth/plaid.env")
    assert production.items == Path("/etc/networth/plaid-items.json")
    assert sandbox.credentials == Path("/etc/networth/plaid-sandbox.env")
    assert sandbox.items == Path("/etc/networth/plaid-items-sandbox.json")


def test_credentials_load(tmp_path: Path) -> None:
    write_credentials(tmp_path, "plaid-sandbox.env", SANDBOX_FILE)
    credentials = load_credentials(PlaidEnvironment.SANDBOX, secrets_dir=tmp_path)
    assert credentials.client_id == "synthetic-client"
    assert credentials.environment is PlaidEnvironment.SANDBOX


def test_mismatched_plaid_env_refuses_to_start(tmp_path: Path) -> None:
    """The one this rule exists for: a Production secret in the file Sandbox
    selects. Caught here, not at the moment it spends a lifetime Item slot."""
    write_credentials(
        tmp_path,
        "plaid-sandbox.env",
        "PLAID_CLIENT_ID=synthetic-client\nPLAID_SECRET=synthetic\nPLAID_ENV=production\n",
    )
    with pytest.raises(ConfigError) as raised:
        load_credentials(PlaidEnvironment.SANDBOX, secrets_dir=tmp_path)
    assert "PLAID_ENV" in str(raised.value)


def test_missing_plaid_env_is_a_mismatch(tmp_path: Path) -> None:
    """A file with no declaration cannot agree with anything. Treating absence
    as consent would make the check pass for exactly the files nobody checked."""
    write_credentials(
        tmp_path, "plaid.env", "PLAID_CLIENT_ID=synthetic-client\nPLAID_SECRET=synthetic\n"
    )
    with pytest.raises(ConfigError):
        load_credentials(PlaidEnvironment.PRODUCTION, secrets_dir=tmp_path)


def test_missing_file_names_the_path_and_whose_job_it_is(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as raised:
        load_credentials(PlaidEnvironment.PRODUCTION, secrets_dir=tmp_path)
    message = str(raised.value)
    assert "plaid.env" in message
    assert "owner" in message


def test_empty_secret_is_not_usable(tmp_path: Path) -> None:
    write_credentials(
        tmp_path,
        "plaid-sandbox.env",
        "PLAID_CLIENT_ID=synthetic-client\nPLAID_SECRET=\nPLAID_ENV=sandbox\n",
    )
    with pytest.raises(ConfigError) as raised:
        load_credentials(PlaidEnvironment.SANDBOX, secrets_dir=tmp_path)
    assert "PLAID_SECRET" in str(raised.value)


def test_no_secret_value_appears_in_any_error(tmp_path: Path) -> None:
    """A parse failure is not a reason to print a credential."""
    secret = "synthetic-secret-value-do-not-print"
    write_credentials(
        tmp_path,
        "plaid-sandbox.env",
        f"PLAID_CLIENT_ID=synthetic-client\nPLAID_SECRET={secret}\nthis line has no equals sign\n",
    )
    with pytest.raises(ConfigError) as raised:
        load_credentials(PlaidEnvironment.SANDBOX, secrets_dir=tmp_path)
    assert secret not in str(raised.value)


def test_credentials_repr_does_not_render_either_half_of_the_credential(tmp_path: Path) -> None:
    """The default dataclass repr would put them in every traceback that touched
    this object (AGENTS.md rule 1). Plaid authenticates with the *pair*, so the
    client id is a credential too — the first version of this repr printed it
    (found in review, round 1)."""
    client_id = "synthetic-client-id-value-do-not-print"
    secret = "synthetic-secret-value-do-not-print"
    write_credentials(
        tmp_path,
        "plaid-sandbox.env",
        f"PLAID_CLIENT_ID={client_id}\nPLAID_SECRET={secret}\nPLAID_ENV=sandbox\n",
    )
    credentials = load_credentials(PlaidEnvironment.SANDBOX, secrets_dir=tmp_path)
    for rendering in (repr(credentials), str(credentials), f"{credentials}"):
        assert secret not in rendering
        assert client_id not in rendering
        assert "sandbox" in rendering
    assert credentials.secret == secret
    assert credentials.client_id == client_id


def test_no_value_from_the_credential_file_appears_in_the_mismatch_error(tmp_path: Path) -> None:
    """The branch that fires when the file is *wrong* is the last place to
    assume its contents are a closed vocabulary. Whatever is on that PLAID_ENV
    line is arbitrary text out of the file that holds the master credential, and
    this message is logged (found in review, round 1)."""
    declared = "synthetic-declared-env-value-do-not-print"
    client_id = "synthetic-client-id-value-do-not-print"
    secret = "synthetic-secret-value-do-not-print"
    write_credentials(
        tmp_path,
        "plaid-sandbox.env",
        f"PLAID_CLIENT_ID={client_id}\nPLAID_SECRET={secret}\nPLAID_ENV={declared}\n",
    )
    with pytest.raises(ConfigError) as raised:
        load_credentials(PlaidEnvironment.SANDBOX, secrets_dir=tmp_path)
    rendered = f"{raised.value!r} {raised.value!s}"
    for value in (declared, client_id, secret):
        assert value not in rendered
    assert "plaid-sandbox.env" in rendered
    assert "sandbox" in rendered


def test_no_credential_value_appears_when_a_required_key_is_empty(tmp_path: Path) -> None:
    """The other error path that has read the whole file by the time it raises."""
    secret = "synthetic-secret-value-do-not-print"
    write_credentials(
        tmp_path,
        "plaid-sandbox.env",
        f"PLAID_CLIENT_ID=\nPLAID_SECRET={secret}\nPLAID_ENV=sandbox\n",
    )
    with pytest.raises(ConfigError) as raised:
        load_credentials(PlaidEnvironment.SANDBOX, secrets_dir=tmp_path)
    assert secret not in f"{raised.value!r} {raised.value!s}"
    assert "PLAID_CLIENT_ID" in str(raised.value)


def test_the_rejected_networth_env_value_is_not_echoed() -> None:
    """It arrives from the process environment, where a mis-pasted unit file can
    put anything at all, and the refusal is logged."""
    bogus = "synthetic-networth-env-value-do-not-print"
    with pytest.raises(ConfigError) as raised:
        selected_environment({"NETWORTH_ENV": bogus})
    rendered = f"{raised.value!r} {raised.value!s}"
    assert bogus not in rendered
    assert "sandbox" in rendered
    assert "production" in rendered


def test_comments_quotes_and_export_are_read_the_way_systemd_reads_them(tmp_path: Path) -> None:
    write_credentials(
        tmp_path,
        "plaid-sandbox.env",
        "# installed by the owner\n"
        'export PLAID_CLIENT_ID="synthetic-client"\n'
        "PLAID_SECRET='synthetic-secret'\n"
        "\n"
        "PLAID_ENV=sandbox\n",
    )
    credentials = load_credentials(PlaidEnvironment.SANDBOX, secrets_dir=tmp_path)
    assert credentials.client_id == "synthetic-client"
    assert credentials.secret == "synthetic-secret"


def test_each_environment_reads_only_its_own_file(tmp_path: Path) -> None:
    """No fallback between the two files — section 15's rule about hosts applies
    here too: a lookup that falls back is how the wrong credential gets used."""
    write_credentials(tmp_path, "plaid-sandbox.env", SANDBOX_FILE)
    with pytest.raises(ConfigError) as raised:
        load_credentials(PlaidEnvironment.PRODUCTION, secrets_dir=tmp_path)
    assert "plaid.env" in str(raised.value)


def test_api_host_is_paired_with_the_environment() -> None:
    assert PlaidEnvironment.SANDBOX.api_host == "https://sandbox.plaid.com"
    assert PlaidEnvironment.PRODUCTION.api_host == "https://production.plaid.com"
