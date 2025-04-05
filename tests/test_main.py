import os
from unittest.mock import Mock, mock_open, patch

import pytest
import yaml
from cloudflare._exceptions import NotFoundError
from cloudflare.types.dns import ARecord
from schema import SchemaError

from main import (
    DnsUpdateRequest,
    get_cloudflare_client,
    get_public_ip,
    load_configuration,
    prepare_updates,
    validate_configuration,
)

# Test data
VALID_CONFIG_SINGLE_ZONE = """
cloudflare:
  - authentication:
      api_token: "test-token"
    zone_id: "test-zone"
    subdomains:
      - name: "test"
        proxied: true
ttl: 300
"""

VALID_CONFIG_MULTIPLE_ZONES = """
cloudflare:
  - authentication:
      api_token: "test-token-1"
    zone_id: "test-zone-1"
    subdomains:
      - name: "foo"
        proxied: true
  - authentication:
      api_token: "test-token-2"
    zone_id: "test-zone-2"
    subdomains:
      - name: "bar"
        proxied: true
ttl: 300
"""

INVALID_CONFIG = """
cloudflare:
  - wrong_key: "value"
"""


class MockZone:
    def __init__(self, name):
        self.name = name


class MockRequest:
    def __init__(self):
        self.method = "GET"
        self.url = "https://api.cloudflare.com/client/v4/zones/invalid_zone"
        self.headers = {}


class MockResponse:
    def __init__(self):
        self.status_code = 404
        self.headers = {}
        self.url = "https://api.cloudflare.com/client/v4/zones/invalid_zone"
        self.request = MockRequest()


@pytest.fixture
def valid_single_zone_config_file(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(VALID_CONFIG_SINGLE_ZONE)
    return config_file


@pytest.fixture
def valid_multiple_zones_config_file(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(VALID_CONFIG_MULTIPLE_ZONES)
    return config_file


def test_load_configuration_single_zone_valid(valid_single_zone_config_file):
    with patch("main.BASE_PATH", valid_single_zone_config_file.parent):
        config = load_configuration()
        assert config["cloudflare"][0]["authentication"]["api_token"] == "test-token"
        assert config["cloudflare"][0]["zone_id"] == "test-zone"
        assert config["ttl"] == 300


def test_load_configuration_multiple_zones_valid(valid_multiple_zones_config_file):
    with patch("main.BASE_PATH", valid_multiple_zones_config_file.parent):
        config = load_configuration()
        assert config["cloudflare"][0]["authentication"]["api_token"] == "test-token-1"
        assert config["cloudflare"][0]["zone_id"] == "test-zone-1"
        assert config["cloudflare"][1]["authentication"]["api_token"] == "test-token-2"
        assert config["cloudflare"][1]["zone_id"] == "test-zone-2"
        assert config["ttl"] == 300


def test_load_configuration_invalid_yaml():
    mock_file = mock_open(read_data="invalid: }")
    with patch("builtins.open", mock_file):
        with pytest.raises(yaml.YAMLError):
            load_configuration()


def test_load_configuration_schema_error(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(INVALID_CONFIG)

    with patch("main.BASE_PATH", config_file.parent):
        with pytest.raises(SchemaError):
            load_configuration()


def test_load_configuration_file_not_found():
    with patch("os.path.exists", return_value=False):
        with pytest.raises(FileNotFoundError):
            load_configuration()


@pytest.mark.parametrize(
    "mock_response,expected",
    [
        (b"1.2.3.4", "1.2.3.4"),
        (Exception(), None),
    ],
)
def test_get_public_ip(mock_response, expected):
    with patch("urllib.request.urlopen") as mock_urlopen:
        if isinstance(mock_response, Exception):
            mock_urlopen.side_effect = mock_response
        else:
            mock_urlopen.return_value.read.return_value = mock_response

        result = get_public_ip()
        assert result == expected


def test_get_cloudflare_client_with_token():
    auth_config = {"api_token": "test-token"}
    with patch.dict(os.environ, {}, clear=True):
        client = get_cloudflare_client(auth_config)
        assert client is not None


def test_get_cloudflare_client_with_key_email():
    auth_config = {"api_key": "test-key", "api_email": "test@example.com"}
    with patch.dict(os.environ, {}, clear=True):
        client = get_cloudflare_client(auth_config)
        assert client is not None


def test_get_cloudflare_client_invalid_config():
    auth_config = {"invalid": "config"}
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError):
            get_cloudflare_client(auth_config)


def test_prepare_updates():
    config = {
        "zone_id": "test-zone",
        "zone_name": "example.com",
        "subdomains": [
            {"name": "test", "proxied": True},
            {"name": "@", "proxied": False},
        ],
    }

    records = [
        ARecord(
            id="record1",
            name="test.example.com",
            type="A",
            content="1.1.1.1",
            proxied=True,
        ),
        ARecord(
            id="record2",
            name="example.com",
            type="A",
            content="1.1.1.1",
            proxied=False,
        ),
    ]

    new_ip = "2.2.2.2"

    updates = prepare_updates(config, records, new_ip)

    assert len(updates) == 2
    assert isinstance(updates[0], DnsUpdateRequest)
    assert updates[0].zone_id == "test-zone"
    assert updates[0].fqdn == "test.example.com"
    assert updates[0].content == "1.1.1.1"
    assert updates[0].proxied is True


def test_prepare_updates_no_changes():
    config = {
        "zone_id": "test-zone",
        "zone_name": "example.com",
        "subdomains": [{"name": "test", "proxied": True}],
    }

    records = [
        ARecord(
            id="record1",
            name="test.example.com",
            type="A",
            content="1.1.1.1",
            proxied=True,
        )
    ]

    updates = prepare_updates(config, records, "1.1.1.1")
    assert len(updates) == 0


def test_validate_configuration_no_valid_zones():
    """Test when both zones are invalid"""
    config = {
        "cloudflare": [
            {
                "authentication": {"api_token": "token1"},
                "zone_id": "invalid_zone1",
                "subdomains": [{"name": "test1"}],
            },
            {
                "authentication": {"api_token": "token2"},
                "zone_id": "invalid_zone2",
                "subdomains": [{"name": "test2"}],
            },
        ]
    }

    with patch("cloudflare.Cloudflare") as mock_cf_class:
        # Create a new mock client for each call to Cloudflare()
        mock_client1 = Mock()
        mock_client2 = Mock()
        mock_cf_class.side_effect = [mock_client1, mock_client2]

        # Configure both clients to raise NotFoundError
        mock_client1.zones.get.side_effect = NotFoundError(
            message="Zone not found",
            response=MockResponse(),
            body={"success": False, "errors": [{"message": "Zone not found"}]},
        )
        mock_client2.zones.get.side_effect = NotFoundError(
            message="Zone not found",
            response=MockResponse(),
            body={"success": False, "errors": [{"message": "Zone not found"}]},
        )

        valid_configs = validate_configuration(config)

        assert len(valid_configs) == 0
