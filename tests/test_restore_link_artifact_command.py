"""``networth restore-link-artifact``: the verb that makes the restore reachable.

Every identifier is synthetic. These run the verb through :func:`networth.cli.main`
rather than calling ``run`` directly, because *"the library function works"* was
already true before this verb existed — what was missing was any path from something
the owner can type to that function, and only the CLI can show that path exists.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from networth.backup import crypto
from networth.cli import main
from networth.link_fence import ATTESTATION, FENCED_INSTANCE, FenceAttestation
from networth.link_sink import (
    DUPLICATE_EXCHANGE,
    INCOMPLETE_RECOVERY,
    EmergencyArtifactSink,
    RecoveredItem,
)
from networth.plaid import environment as environment_module
from networth.plaid.environment import (
    DATA_DIR_NAME,
    ENV_VAR,
    PlaidEnvironment,
    paths_for,
    selected_environment,
)
from networth.storage import migrate
from networth.tokenstore import Secret, SecretKind, TokenStore, secret_ref_for

NOW = datetime(2026, 9, 30, 3, 0, tzinfo=UTC)
STAMP = "2026-09-30T03:00:00Z"
ACCESS_TOKEN = "access-sandbox-" + secrets.token_hex(16)
ITEM_ID = "item-0000000000000001"
FLOW_ID = "0a1b2c3d4e5f60718293a4b5c6d7e8f9"
RESULT_ID = "5f60718293a4b5c6d7e8f90a1b2c3d4e"
FENCE = FenceAttestation(
    instance=FENCED_INSTANCE,
    statement=ATTESTATION,
    confirmed_at=datetime(2026, 9, 30, 2, 55, tzinfo=UTC),
)


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A replacement host, with the two roots ``paths_for`` reads pointed at ``tmp_path``.

    The roots are redirected rather than the three paths patched apart, because
    ``paths_for`` choosing all three together is the property `AGENTS.md` relies on —
    *"it selects the credential file, the items file and the database path
    together"* — and a test that set them independently could pass while the verb
    read a Sandbox store with a Production database.
    """
    secrets_dir = tmp_path / "etc-networth"
    secrets_dir.mkdir()
    (tmp_path / DATA_DIR_NAME).mkdir()
    monkeypatch.setattr(environment_module, "SECRETS_DIR", secrets_dir)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv(ENV_VAR, "sandbox")
    yield tmp_path


def database(path: Path, *, state: str | None = "SUCCESS_PENDING_EXCHANGE") -> None:
    connection = sqlite3.connect(path)
    migrate(connection)
    connection.execute(
        "INSERT INTO link_request(flow_id, minted_at, hosted_url_expires_at, state) "
        "VALUES (?, ?, ?, 'URL_MINTED')",
        (FLOW_ID, STAMP, STAMP),
    )
    if state is not None:
        connection.execute(
            "INSERT INTO link_result(result_id, flow_id, token_digest, state) "
            "VALUES (?, ?, 'synthetic-digest', ?)",
            (RESULT_ID, FLOW_ID, state),
        )
    connection.commit()
    connection.close()


def key_file(tmp_path: Path) -> Path:
    path = tmp_path / "backup.key"
    path.write_text(secrets.token_hex(crypto.KEY_BYTES) + "\n")
    path.chmod(0o600)
    return path


def sealed(tmp_path: Path, key: Path) -> Path:
    artifact = tmp_path / "recovery.artifact"
    sink = EmergencyArtifactSink(artifact, key_file=key)
    sink.prepare()
    sink.commit(
        RecoveredItem(
            access_token=Secret(ACCESS_TOKEN),
            item_id=ITEM_ID,
            flow_id=FLOW_ID,
            link_session_id="session-0001",
            request_id="request-0001",
            fence=FENCE,
        ),
        now=NOW,
    )
    return artifact


def paths(host_dir: Path) -> tuple[Path, Path]:
    """The two destinations ``NETWORTH_ENV`` selects — asked of ``paths_for`` itself.

    Recomputing them here from ``tmp_path`` would be a second copy of section 15's
    layout, and a test that hardcoded the filenames would keep passing if the verb
    started writing somewhere else entirely.
    """
    selected = paths_for(selected_environment())
    del host_dir
    return selected.database, selected.items


def test_the_verb_is_reachable_from_the_cli_and_completes_both_halves(
    host: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key = key_file(host)
    artifact = sealed(host, key)
    db_path, tokens = paths(host)
    database(db_path)

    code = main(["restore-link-artifact", "--artifact", str(artifact), "--backup-key", str(key)])

    assert code == 0
    out = capsys.readouterr().out
    assert "flow pairing  WRITTEN" in out
    assert "Recovery is complete" in out
    # Both halves, read off the disk rather than out of the transcript.
    assert (
        TokenStore(tokens).get(secret_ref_for(SecretKind.ACCESS_TOKEN, FLOW_ID)).reveal()
        == ACCESS_TOKEN
    )
    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute(
            "SELECT item_id FROM link_result WHERE result_id = ?", (RESULT_ID,)
        ).fetchone() == (ITEM_ID,)
    finally:
        connection.close()


def test_a_missing_database_is_refused_rather_than_read_as_nothing_to_pair(
    host: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The refusal `AGENTS.md` rule 1's no-fallback rule asks for.

    A wrong ``NETWORTH_ENV`` or data directory produces exactly this shape, and the
    tolerant version reports it as *"no row for your flow, pairing owed"* — a
    measurement about a database nobody opened.
    """
    key = key_file(host)
    artifact = sealed(host, key)

    code = main(["restore-link-artifact", "--artifact", str(artifact), "--backup-key", str(key)])

    assert code == 2
    captured = capsys.readouterr()
    assert "no database at" in captured.err
    assert "--without-database" in captured.err
    # Nothing was written: the refusal must land before the credential moves, so the
    # operator can fix the path and re-run without having spent anything.
    assert not paths(host)[1].exists()


def test_without_database_restores_the_credential_and_owes_the_pairing(
    host: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key = key_file(host)
    artifact = sealed(host, key)

    code = main(
        [
            "restore-link-artifact",
            "--artifact",
            str(artifact),
            "--backup-key",
            str(key),
            "--without-database",
        ]
    )

    assert code == INCOMPLETE_RECOVERY
    out = capsys.readouterr().out
    assert "still owed    flow_pairing" in out
    assert "--pairing-only" in out
    assert (
        TokenStore(paths(host)[1]).get(secret_ref_for(SecretKind.ACCESS_TOKEN, FLOW_ID)).reveal()
        == ACCESS_TOKEN
    )


def test_pairing_only_finishes_what_the_credential_only_run_left_owed(
    host: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The sequence the guidance above actually tells the owner to run.

    This is the test that makes that guidance true rather than plausible: the full
    restore **cannot** be re-run here, because the credential it already stored makes
    the second ``prepare_for`` refuse. Both steps run in order, and the second one has
    to be the one the printed instruction names.
    """
    key = key_file(host)
    artifact = sealed(host, key)
    db_path, _ = paths(host)

    first = main(
        [
            "restore-link-artifact",
            "--artifact",
            str(artifact),
            "--backup-key",
            str(key),
            "--without-database",
        ]
    )
    assert first == INCOMPLETE_RECOVERY
    capsys.readouterr()

    # The database comes up only now, which is the order this flag exists for.
    database(db_path)

    # The control: the full restore refuses, so the advice could not have been
    # "run it again". If this ever stops refusing, `--pairing-only` has lost its
    # reason to exist and the guidance should change with it.
    assert (
        main(["restore-link-artifact", "--artifact", str(artifact), "--backup-key", str(key)]) == 2
    )
    assert "already holds" in capsys.readouterr().err

    code = main(
        [
            "restore-link-artifact",
            "--artifact",
            str(artifact),
            "--backup-key",
            str(key),
            "--pairing-only",
        ]
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "the credential is not touched" in out
    assert "flow pairing  WRITTEN" in out
    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute(
            "SELECT item_id FROM link_result WHERE result_id = ?", (RESULT_ID,)
        ).fetchone() == (ITEM_ID,)
    finally:
        connection.close()


def test_a_database_older_than_the_link_is_not_reported_as_a_finished_recovery(
    host: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key = key_file(host)
    artifact = sealed(host, key)
    db_path, _ = paths(host)
    database(db_path, state=None)

    code = main(["restore-link-artifact", "--artifact", str(artifact), "--backup-key", str(key)])

    assert code == INCOMPLETE_RECOVERY
    out = capsys.readouterr().out
    assert "flow pairing  NO_ROW" in out
    assert "KEEP the artifact" in out


def test_a_disputed_row_leaves_the_credential_durable_and_says_so(
    host: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key = key_file(host)
    artifact = sealed(host, key)
    db_path, tokens = paths(host)
    database(db_path, state="EXCHANGED")

    code = main(["restore-link-artifact", "--artifact", str(artifact), "--backup-key", str(key)])

    assert code == INCOMPLETE_RECOVERY
    out = capsys.readouterr().out
    assert "flow pairing  REFUSED" in out
    assert "criterion 7" in out
    assert "do NOT exchange again" in out
    # The refusal is about accounting, so the credential must still have landed:
    # presenting this as a failed restore would invite a re-run of a spent token.
    assert (
        TokenStore(tokens).get(secret_ref_for(SecretKind.ACCESS_TOKEN, FLOW_ID)).reveal()
        == ACCESS_TOKEN
    )


def test_a_torn_artifact_is_a_repeatable_failure_and_writes_nothing(
    host: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key = key_file(host)
    artifact = host / "recovery.artifact"
    artifact.write_bytes(b"torn")
    db_path, tokens = paths(host)
    database(db_path)

    code = main(["restore-link-artifact", "--artifact", str(artifact), "--backup-key", str(key)])

    assert code == 2
    err = capsys.readouterr().err
    assert "can be re-run" in err
    assert not tokens.exists()


def test_the_environment_is_required_and_not_defaulted(
    host: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    key = key_file(host)
    artifact = sealed(host, key)
    monkeypatch.delenv(ENV_VAR)

    code = main(["restore-link-artifact", "--artifact", str(artifact), "--backup-key", str(key)])

    assert code == 2
    assert ENV_VAR in capsys.readouterr().err


def test_production_is_not_refused_because_it_is_the_whole_scenario(
    host: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The opposite of ``complete-hosted-link``'s gate, and deliberately so.

    That verb refuses anything but Sandbox because it *exchanges* real tokens. This
    one exchanges nothing — it moves a credential that already exists — and `07b` is
    about a **Production** Item lost with the VPS. A Production gate here would refuse
    the only scenario the task exists for, so the test is that the run proceeds far
    enough to reach the destination the environment selected.
    """
    key = key_file(host)
    artifact = sealed(host, key)
    monkeypatch.setenv(ENV_VAR, "production")

    code = main(["restore-link-artifact", "--artifact", str(artifact), "--backup-key", str(key)])

    out = capsys.readouterr()
    assert "environment   production" in out.out
    assert "refusing to run against" not in out.err
    # It stops on the absent Production database, not on the environment — which is
    # the discriminating observation: a gate would have refused before printing a
    # destination at all.
    assert code == 2
    assert "no database at" in out.err
    # The **Production** database, asked of `paths_for` rather than spelled out: the
    # two environments' filenames differ by a suffix, and a literal would let this
    # pass while the verb looked at the Sandbox one.
    production = paths_for(PlaidEnvironment.PRODUCTION)
    assert str(production.database) in out.err
    assert str(paths_for(PlaidEnvironment.SANDBOX).database) not in out.err


def test_the_artifact_is_validated_as_fenced_before_anything_is_paired(
    host: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Criterion 6 on the ``--pairing-only`` path too.

    It touches no credential, so nothing would stop it reading identifiers out of an
    unfenced file — and an accounting write sourced from a file that is not a complete
    fenced recovery is the same defect one column over.
    """
    key = key_file(host)
    crypto_key = crypto.load_backup_key(key)
    unfenced = host / "recovery.artifact"
    unfenced.write_bytes(
        crypto.seal(
            json.dumps(
                {
                    "schema": "networth.link-recovery-artifact.2",
                    "flow_id": FLOW_ID,
                    "item_id": ITEM_ID,
                    "access_token": ACCESS_TOKEN,
                    "link_session_id": None,
                    "request_id": None,
                }
            ).encode(),
            crypto_key,
        )
    )
    db_path, _ = paths(host)
    database(db_path)

    code = main(
        [
            "restore-link-artifact",
            "--artifact",
            str(unfenced),
            "--backup-key",
            str(key),
            "--pairing-only",
        ]
    )

    assert code == 2
    assert "not fenced" in capsys.readouterr().err
    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute(
            "SELECT item_id FROM link_result WHERE result_id = ?", (RESULT_ID,)
        ).fetchone() == (None,)
    finally:
        connection.close()


def test_a_distinct_item_duplicate_is_refused_and_the_transcript_says_what_to_keep(
    host: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`07b` criterion 7 through the verb, in the shape that costs a lifetime slot.

    The old VPS came back and its exchange returned a **different** Item, so its
    credential is already under ``access_token.<flow_id>`` here. The old refusal for
    this said *"the exchange is already done"* and then *"this verb can be re-run
    once the reason above is fixed"* — and "the reason" is a credential that is the
    only copy for its own Item. Both halves are asserted: the classification, and the
    absence of the instruction that would destroy it.
    """
    key = key_file(host)
    artifact = sealed(host, key)
    db_path, tokens = paths(host)
    database(db_path)
    other_token = "access-sandbox-" + secrets.token_hex(16)
    TokenStore(tokens).put(
        SecretKind.ACCESS_TOKEN, FLOW_ID, other_token, item_id="item-0000000000000002"
    )

    code = main(["restore-link-artifact", "--artifact", str(artifact), "--backup-key", str(key)])

    assert code == DUPLICATE_EXCHANGE
    err = capsys.readouterr().err
    assert "duplicate     DISTINCT_ITEMS" in err
    assert "slots spent   2" in err
    assert str(artifact) in err  # the artifact is named as a thing to keep
    assert "re-run" not in err
    # The other host's credential was not touched, which is the whole point of
    # refusing rather than "fixing the reason".
    assert (
        TokenStore(tokens).get(secret_ref_for(SecretKind.ACCESS_TOKEN, FLOW_ID)).reveal()
        == other_token
    )
