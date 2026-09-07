"""Explicit VPS backup paths, loaded without reading secret values."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from networth.config import SECRETS_DIR, ConfigError, read_env_file
from networth.plaid.environment import paths_for, selected_environment

RUNTIME_CONFIG = SECRETS_DIR / "networth.env"
BACKUP_KEY = SECRETS_DIR / "networth-backup.key"
ARCHIVE_DIR_VAR = "NETWORTH_ARCHIVE_DIR"


@dataclass(frozen=True, slots=True)
class BackupConfig:
    database: Path
    token_store: Path
    archive_dir: Path
    key_file: Path


def load_backup_config(
    *,
    environ: dict[str, str] | None = None,
    config_path: Path = RUNTIME_CONFIG,
    secrets_dir: Path = SECRETS_DIR,
    data_dir: Path | None = None,
) -> BackupConfig:
    """Load production paths from one explicit environment selection.

    SSH forced commands do not inherit a service unit's environment.  The
    non-secret ``networth.env`` therefore carries the same explicit
    ``NETWORTH_ENV`` selection and archive directory; it is parsed as data,
    never sourced as shell. The file is authoritative in production so the
    timer and forced command cannot select different databases from ambient
    process state. An explicit mapping may override entries in tests only.
    """

    values = read_env_file(config_path, describe="networth runtime configuration")
    if environ is not None:
        values.update(environ)
    environment = selected_environment(values)
    paths = paths_for(environment, secrets_dir=secrets_dir, data_dir=data_dir)
    archive_dir = values.get(ARCHIVE_DIR_VAR)
    if not archive_dir:
        raise ConfigError(f"{config_path} has no usable {ARCHIVE_DIR_VAR}")
    return BackupConfig(
        database=paths.database,
        token_store=paths.items,
        archive_dir=Path(archive_dir),
        key_file=secrets_dir / BACKUP_KEY.name,
    )
