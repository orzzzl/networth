"""The approved 07a metadata reads use SDK requests and redact provider data."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from plaid.exceptions import ApiException
from plaid.model_utils import model_to_dict

from networth.plaid.client import ItemInstitution, PlaidCallError, PlaidClient
from networth.plaid.environment import PlaidCredentials, PlaidEnvironment
from tests.fake_plaid import FakeSandboxApi

TOKEN = "synthetic-metadata-material"
ITEM = "synthetic-metadata-item"
INSTITUTION = "synthetic-metadata-institution"
NAME = "Synthetic institution sentinel"
CREDENTIALS = PlaidCredentials("synthetic-client", "synthetic-secret", PlaidEnvironment.SANDBOX)


class MetadataApi(FakeSandboxApi):
    def __init__(self) -> None:
        super().__init__()
        self.item_response: Any = SimpleNamespace(
            item=SimpleNamespace(item_id=ITEM, institution_id=INSTITUTION)
        )
        self.institution_response: Any = SimpleNamespace(
            institution=SimpleNamespace(institution_id=INSTITUTION, name=NAME, oauth=True)
        )
        self.metadata_requests: list[dict[str, Any]] = []

    def item_get(self, item_get_request: Any) -> Any:
        self.metadata_requests.append(model_to_dict(item_get_request))
        if isinstance(self.item_response, Exception):
            raise self.item_response
        return self.item_response

    def institutions_get_by_id(self, request: Any) -> Any:
        self.metadata_requests.append(model_to_dict(request))
        if isinstance(self.institution_response, Exception):
            raise self.institution_response
        return self.institution_response


def read(api: MetadataApi) -> ItemInstitution:
    return PlaidClient(CREDENTIALS, api=api).item_institution(
        TOKEN, expected_item_id=ITEM, country_codes=("US", "CA")
    )


def test_serialized_requests_and_redacted_result(capsys: Any, caplog: Any) -> None:
    api = MetadataApi()
    result = read(api)
    assert result == ItemInstitution(ITEM, INSTITUTION, NAME, True)
    assert api.metadata_requests == [
        {"access_token": TOKEN},
        {"institution_id": INSTITUTION, "country_codes": ["US", "CA"]},
    ]
    output = capsys.readouterr()
    rendered = repr(result) + str(result) + output.out + output.err + caplog.text
    for value in (TOKEN, ITEM, INSTITUTION, NAME):
        assert value not in rendered


@pytest.mark.parametrize("field", ["item_id", "institution_id"])
@pytest.mark.parametrize("value", [None, "", 123, TOKEN])
def test_invalid_item_metadata_never_reaches_institution_read(field: str, value: Any) -> None:
    api = MetadataApi()
    setattr(api.item_response.item, field, value)
    with pytest.raises(PlaidCallError) as exc:
        read(api)
    assert len(api.metadata_requests) == 1
    assert TOKEN not in str(exc.value)


def test_wrong_item_identity_never_reaches_institution_read() -> None:
    api = MetadataApi()
    api.item_response.item.item_id = "synthetic-other-item"
    with pytest.raises(PlaidCallError, match="identity mismatch"):
        read(api)
    assert len(api.metadata_requests) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("institution_id", "synthetic-other-institution"),
        ("institution_id", None),
        ("name", None),
        ("name", ""),
        ("name", TOKEN),
        ("oauth", None),
        ("oauth", 1),
        ("oauth", "true"),
    ],
)
def test_invalid_institution_metadata_is_redacted(field: str, value: Any) -> None:
    api = MetadataApi()
    setattr(api.institution_response.institution, field, value)
    with pytest.raises(PlaidCallError) as exc:
        read(api)
    for sentinel in (TOKEN, INSTITUTION, NAME):
        assert sentinel not in str(exc.value)


@pytest.mark.parametrize("endpoint", ["item_response", "institution_response"])
@pytest.mark.parametrize("transport", [False, True])
def test_metadata_failures_do_not_render_provider_values(endpoint: str, transport: bool) -> None:
    api = MetadataApi()
    if transport:
        error: Exception = OSError(f"{NAME} {TOKEN}")
    else:
        api_error = ApiException(status=503)
        api_error.body = f'{{"institution": "{NAME}", "access_token": "{TOKEN}"}}'
        error = api_error
    setattr(api, endpoint, error)
    with pytest.raises(PlaidCallError) as exc:
        read(api)
    assert NAME not in str(exc.value)
    assert TOKEN not in str(exc.value)
    assert exc.value.__suppress_context__


def test_non_oauth_institution_is_not_promoted() -> None:
    api = MetadataApi()
    api.institution_response.institution.oauth = False
    assert read(api).is_oauth is False
