import os
from unittest.mock import patch

import pytest
from cloudflare.types.dns import ARecord

from configuration_manager import (
    AuthenticationConfig,
    CloudflareZoneConfig,
    SubdomainConfig,
)
from main import (
    DnsUpdateRequest,
    get_cloudflare_client,
    prepare_updates,
)

# Test data
TEST_ZONE_NAME = "example.com"
TEST_IP = "1.1.1.1"


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
    auth = AuthenticationConfig(api_token="test-token")
    subdomains = [
        SubdomainConfig(name="test", proxied=True, ttl=120),
        SubdomainConfig(name="@", proxied=False),
    ]
    zone_config = CloudflareZoneConfig(
        authentication=auth,
        zone_id="test-zone",
        subdomains=subdomains,
        ttl=300,
    )
    zone_config.zone_name = TEST_ZONE_NAME

    records = [
        ARecord(
            id="record1",
            name="test.example.com",
            type="A",
            content=TEST_IP,
            proxied=True,
        ),
        ARecord(
            id="record2",
            name="example.com",
            type="A",
            content=TEST_IP,
            proxied=False,
        ),
    ]

    new_ip = "2.2.2.2"

    updates = prepare_updates(zone_config, records, new_ip)

    assert len(updates) == 2
    assert isinstance(updates[0], DnsUpdateRequest)
    assert updates[0].zone_id == "test-zone"
    assert updates[0].fqdn == "test." + TEST_ZONE_NAME
    assert updates[0].content == TEST_IP
    assert updates[0].proxied is True
    assert updates[0].ttl == 120
    assert updates[1].ttl == 300


def test_prepare_updates_no_changes():
    auth = AuthenticationConfig(api_token="test-token")
    subdomains = [SubdomainConfig(name="test", proxied=True, ttl=120)]
    zone_config = CloudflareZoneConfig(
        authentication=auth,
        zone_id="test-zone",
        subdomains=subdomains,
        ttl=300,
    )
    zone_config.zone_name = TEST_ZONE_NAME

    records = [
        ARecord(
            id="record1",
            name=f"test.{TEST_ZONE_NAME}",
            type="A",
            content=TEST_IP,
            proxied=True,
        )
    ]

    updates = prepare_updates(zone_config, records, TEST_IP)
    assert len(updates) == 0


def test_prepare_updates_with_ttl_precedence():
    """Test TTL precedence (subdomain TTL vs global TTL)"""
    auth = AuthenticationConfig(api_token="test-token")
    subdomains = [
        SubdomainConfig(name="specific", proxied=True, ttl=120),  # Specific TTL
        SubdomainConfig(name="auto", proxied=False, ttl=1),  # Auto TTL
        SubdomainConfig(name="global", proxied=False),  # Uses global TTL
    ]
    zone_config = CloudflareZoneConfig(
        authentication=auth,
        zone_id="test-zone",
        subdomains=subdomains,
        ttl=300,  # Global TTL
    )
    zone_config.zone_name = TEST_ZONE_NAME

    records = [
        ARecord(
            id="record1",
            name=f"specific.{TEST_ZONE_NAME}",
            type="A",
            content=TEST_IP,
            proxied=False,
        ),
        ARecord(
            id="record2",
            name=f"auto.{TEST_ZONE_NAME}",
            type="A",
            content=TEST_IP,
            proxied=False,
        ),
        ARecord(
            id="record3",
            name=f"global.{TEST_ZONE_NAME}",
            type="A",
            content=TEST_IP,
            proxied=False,
        ),
    ]

    new_ip = "2.2.2.2"

    updates = prepare_updates(zone_config, records, new_ip)

    assert len(updates) == 3
    # Check specific TTL subdomain
    assert [u for u in updates if u.fqdn == f"specific.{TEST_ZONE_NAME}"][0].ttl == 120
    # Check Auto TTL subdomain
    assert [u for u in updates if u.fqdn == f"auto.{TEST_ZONE_NAME}"][0].ttl == 1
    # Check global TTL subdomain
    assert [u for u in updates if u.fqdn == f"global.{TEST_ZONE_NAME}"][0].ttl == 300


def test_prepare_updates_with_default_ttl():
    """Test TTL fallback to default when neither subdomain nor global TTL is set"""
    auth = AuthenticationConfig(api_token="test-token")
    subdomains = [
        SubdomainConfig(
            name="test",
            proxied=False,
        )  # No TTL specified
    ]
    zone_config = CloudflareZoneConfig(
        authentication=auth,
        zone_id="test-zone",
        subdomains=subdomains,
        ttl=300,  # Global TTL
    )
    zone_config.zone_name = TEST_ZONE_NAME

    records = [
        ARecord(
            id="record1",
            name=f"test.{TEST_ZONE_NAME}",
            type="A",
            content=TEST_IP,
            proxied=False,
        ),
    ]

    new_ip = "2.2.2.2"

    updates = prepare_updates(zone_config, records, new_ip)

    assert len(updates) == 1
    assert updates[0].ttl == 300  # Should use default TTL (300)
