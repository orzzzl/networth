"""Plaid access: the client wrapper and the one error taxonomy (section 8.2)."""

from networth.plaid.client import ItemStatus, PlaidClient
from networth.plaid.environment import (
    ConfigError,
    Paths,
    PlaidCredentials,
    PlaidEnvironment,
    load_credentials,
    paths_for,
    selected_environment,
)
from networth.plaid.errors import (
    HEALTHY,
    Classification,
    ItemState,
    classify_error,
    malformed_response,
    transport_failure,
)

__all__ = [
    "HEALTHY",
    "Classification",
    "ConfigError",
    "ItemState",
    "ItemStatus",
    "Paths",
    "PlaidClient",
    "PlaidCredentials",
    "PlaidEnvironment",
    "classify_error",
    "load_credentials",
    "malformed_response",
    "paths_for",
    "selected_environment",
    "transport_failure",
]
