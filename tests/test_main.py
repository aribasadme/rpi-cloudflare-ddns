import os
from unittest.mock import patch

import pytest

from main import (
    get_cloudflare_client,
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
