"""Forced commands load explicit VPS paths without a Mac fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

from networth.backup.config import ARCHIVE_DIR_VAR, load_backup_config
from networth.config import ConfigError


def test_runtime_file_selects_database_token_store_and_archive_together(tmp_path: Path) -> None:
    secrets = tmp_path / "vps-secrets"
    data = tmp_path / "vps-data"
    secrets.mkdir()
    config = secrets / "networth.env"
    archive = data / "archives"
    config.write_text(
        f"NETWORTH_ENV=production\n{ARCHIVE_DIR_VAR}={archive}\n",
        encoding="utf-8",
    )
    loaded = load_backup_config(environ={}, config_path=config, secrets_dir=secrets, data_dir=data)
    assert loaded.database == data / "networth.db"
    assert loaded.token_store == secrets / "plaid-items.json"
    assert loaded.archive_dir == archive
    assert loaded.key_file == secrets / "networth-backup.key"
    assert "agents/secrets" not in str(loaded)


def test_environment_selection_and_archive_path_have_no_default(tmp_path: Path) -> None:
    config = tmp_path / "networth.env"
    config.write_text("NETWORTH_ENV=production\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=ARCHIVE_DIR_VAR):
        load_backup_config(environ={}, config_path=config, secrets_dir=tmp_path)

    config.write_text(f"{ARCHIVE_DIR_VAR}=/tmp/synthetic-archives\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="NETWORTH_ENV"):
        load_backup_config(environ={}, config_path=config, secrets_dir=tmp_path)
