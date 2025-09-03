"""
Tests for the new IP Provider system
"""

import time
import urllib.error
from unittest.mock import Mock, patch

import pytest

from ip_provider import (
    CachedIPProvider,
    FallbackIPProvider,
    HttpIPProvider,
    IPProviderError,
    create_ip_provider,
)


class TestHttpIPProvider:
    """Test the basic HTTP IP provider"""

    def test_get_public_ip_success(self):
        """Test successful IP retrieval"""
        provider = HttpIPProvider("https://api.ipify.org", timeout=5)

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = Mock()
            mock_response.read.return_value = b"192.168.1.1"
            mock_urlopen.return_value = mock_response

            ip = provider.get_public_ip()

            assert ip == "192.168.1.1"
            mock_urlopen.assert_called_once_with("https://api.ipify.org", timeout=5)

    def test_get_public_ip_with_whitespace(self):
        """Test IP retrieval with whitespace trimming"""
        provider = HttpIPProvider()

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = Mock()
            mock_response.read.return_value = b"  192.168.1.1  \n"
            mock_urlopen.return_value = mock_response

            ip = provider.get_public_ip()

            assert ip == "192.168.1.1"

    def test_get_public_ip_network_error(self):
        """Test network error handling"""
        provider = HttpIPProvider()

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError("Network error")

            with pytest.raises(IPProviderError) as exc_info:
                provider.get_public_ip()

            assert "Network error while fetching IP" in str(exc_info.value)

    def test_get_public_ip_timeout(self):
        """Test timeout handling"""
        provider = HttpIPProvider(timeout=1)

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = TimeoutError("Request timed out")

            with pytest.raises(IPProviderError) as exc_info:
                provider.get_public_ip()

            assert "Unexpected error fetching IP" in str(exc_info.value)

    def test_get_public_ip_invalid_format(self):
        """Test invalid IP format handling"""
        provider = HttpIPProvider()

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = Mock()
            mock_response.read.return_value = b"not-an-ip"
            mock_urlopen.return_value = mock_response

            with pytest.raises(IPProviderError) as exc_info:
                provider.get_public_ip()

            assert "Invalid IP format received" in str(exc_info.value)

    def test_get_public_ip_unicode_error(self):
        """Test unicode decode error handling"""
        provider = HttpIPProvider()

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = Mock()
            mock_response.read.return_value = b"\xff\xfe\x00\x00"  # Invalid UTF-8
            mock_urlopen.return_value = mock_response

            with pytest.raises(IPProviderError) as exc_info:
                provider.get_public_ip()

            assert "Invalid response format" in str(exc_info.value)

    @pytest.mark.parametrize(
        "ip,expected",
        [
            ("192.168.1.1", True),
            ("0.0.0.0", True),
            ("255.255.255.255", True),
            ("256.1.1.1", False),
            ("192.168.1", False),
            ("192.168.1.1.1", False),
            ("not.an.ip.address", False),
            ("", False),
        ],
    )
    def test_is_valid_ip(self, ip, expected):
        """Test IP validation logic"""
        provider = HttpIPProvider()
        assert provider._is_valid_ip(ip) == expected


class TestCachedIPProvider:
    """Test the caching IP provider decorator"""

    def test_cache_miss_then_hit(self):
        """Test cache behavior on first and second call"""
        mock_provider = Mock()
        mock_provider.get_public_ip.return_value = "192.168.1.1"

        cached_provider = CachedIPProvider(mock_provider, cache_duration=300)

        # First call - cache miss
        ip1 = cached_provider.get_public_ip()
        assert ip1 == "192.168.1.1"
        assert mock_provider.get_public_ip.call_count == 1

        # Second call - cache hit
        ip2 = cached_provider.get_public_ip()
        assert ip2 == "192.168.1.1"
        assert mock_provider.get_public_ip.call_count == 1  # Still 1!

    def test_cache_expiry(self):
        """Test cache expiration"""
        mock_provider = Mock()
        mock_provider.get_public_ip.side_effect = ["192.168.1.1", "192.168.1.2"]

        cached_provider = CachedIPProvider(mock_provider, cache_duration=1)

        # First call
        ip1 = cached_provider.get_public_ip()
        assert ip1 == "192.168.1.1"

        # Wait for cache to expire
        time.sleep(1.1)

        # Second call should fetch new IP
        ip2 = cached_provider.get_public_ip()
        assert ip2 == "192.168.1.2"
        assert mock_provider.get_public_ip.call_count == 2

    def test_fallback_to_stale_cache_on_error(self):
        """Test using stale cache when fresh fetch fails"""
        mock_provider = Mock()
        mock_provider.get_public_ip.side_effect = [
            "192.168.1.1",  # First successful call
            IPProviderError("Network failed"),  # Second call fails
        ]

        cached_provider = CachedIPProvider(mock_provider, cache_duration=1)

        # First call succeeds
        ip1 = cached_provider.get_public_ip()
        assert ip1 == "192.168.1.1"

        # Wait for cache to expire
        time.sleep(1.1)

        # Second call should fallback to stale cache
        ip2 = cached_provider.get_public_ip()
        assert ip2 == "192.168.1.1"  # Same as cached value

    def test_error_with_no_cache(self):
        """Test error when no cache is available"""
        mock_provider = Mock()
        mock_provider.get_public_ip.side_effect = IPProviderError("Network failed")

        cached_provider = CachedIPProvider(mock_provider)

        with pytest.raises(IPProviderError):
            cached_provider.get_public_ip()


class TestFallbackIPProvider:
    """Test the fallback IP provider"""

    def test_first_provider_succeeds(self):
        """Test when first provider succeeds"""
        provider1 = Mock()
        provider1.get_public_ip.return_value = "192.168.1.1"
        provider2 = Mock()

        fallback = FallbackIPProvider([provider1, provider2])

        ip = fallback.get_public_ip()
        assert ip == "192.168.1.1"
        provider1.get_public_ip.assert_called_once()
        provider2.get_public_ip.assert_not_called()

    def test_fallback_to_second_provider(self):
        """Test fallback when first provider fails"""
        provider1 = Mock()
        provider1.get_public_ip.side_effect = IPProviderError("Provider 1 failed")
        provider2 = Mock()
        provider2.get_public_ip.return_value = "192.168.1.2"

        fallback = FallbackIPProvider([provider1, provider2])

        ip = fallback.get_public_ip()
        assert ip == "192.168.1.2"
        provider1.get_public_ip.assert_called_once()
        provider2.get_public_ip.assert_called_once()

    def test_all_providers_fail(self):
        """Test when all providers fail"""
        provider1 = Mock()
        provider1.get_public_ip.side_effect = IPProviderError("Provider 1 failed")
        provider2 = Mock()
        provider2.get_public_ip.side_effect = IPProviderError("Provider 2 failed")

        fallback = FallbackIPProvider([provider1, provider2])

        with pytest.raises(IPProviderError) as exc_info:
            fallback.get_public_ip()

        assert "All IP providers failed" in str(exc_info.value)
        assert "Provider 1 failed" in str(exc_info.value)
        assert "Provider 2 failed" in str(exc_info.value)

    def test_empty_provider_list_raises_error(self):
        """Test that empty provider list raises ValueError"""
        with pytest.raises(ValueError) as exc_info:
            FallbackIPProvider([])

        assert "At least one provider must be specified" in str(exc_info.value)


class TestCreateIPProvider:
    """Test the factory function"""

    def test_create_basic_provider(self):
        """Test creating a basic provider"""
        provider = create_ip_provider(with_cache=False, with_fallback=False)

        # Should be an HttpIPProvider (not wrapped)
        assert isinstance(provider, HttpIPProvider)

    def test_create_cached_provider(self):
        """Test creating a cached provider"""
        provider = create_ip_provider(with_cache=True, with_fallback=False)

        # Should be wrapped in CachedIPProvider
        assert isinstance(provider, CachedIPProvider)
        assert isinstance(provider.provider, HttpIPProvider)

    def test_create_fallback_provider(self):
        """Test creating a fallback provider"""
        provider = create_ip_provider(with_cache=False, with_fallback=True)

        # Should be wrapped in FallbackIPProvider
        assert isinstance(provider, FallbackIPProvider)
        assert len(provider.providers) == 3  # Should have 3 fallback providers

    def test_create_cached_fallback_provider(self):
        """Test creating a cached fallback provider (full features)"""
        provider = create_ip_provider(with_cache=True, with_fallback=True)

        # Should be CachedIPProvider wrapping FallbackIPProvider
        assert isinstance(provider, CachedIPProvider)
        assert isinstance(provider.provider, FallbackIPProvider)
        assert len(provider.provider.providers) == 3


class TestIPProviderIntegration:
    """Integration tests simulating real usage"""

    def test_production_like_usage(self):
        """Test usage pattern similar to production"""
        # Create a provider like we would in production
        provider = create_ip_provider()

        # Mock successful response from first provider
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = Mock()
            mock_response.read.return_value = b"203.0.113.1"
            mock_urlopen.return_value = mock_response

            # First call should succeed and cache
            ip1 = provider.get_public_ip()
            assert ip1 == "203.0.113.1"
            assert mock_urlopen.call_count == 1

            # Second call should use cache
            ip2 = provider.get_public_ip()
            assert ip2 == "203.0.113.1"
            assert mock_urlopen.call_count == 1  # No additional calls

    def test_fallback_behavior_integration(self):
        """Test fallback behavior in realistic scenario"""
        provider = create_ip_provider(with_cache=False, with_fallback=True)

        with patch("urllib.request.urlopen") as mock_urlopen:
            # First provider fails, second succeeds
            mock_urlopen.side_effect = [
                urllib.error.URLError("Connection failed"),  # api.ipify.org fails
                Mock(read=lambda: b"203.0.113.2"),  # checkip.amazonaws.com succeeds
            ]

            ip = provider.get_public_ip()
            assert ip == "203.0.113.2"
            assert mock_urlopen.call_count == 2  # Tried both providers


# Environment variable configuration tests
class TestConfigurableIPProvider:
    """Test environment-based configuration"""

    @patch.dict(
        "os.environ",
        {
            "IP_CACHE_DURATION": "600",
            "IP_ENABLE_CACHE": "true",
            "IP_ENABLE_FALLBACK": "false",
        },
        clear=True,
    )
    def test_env_configuration(self):
        """Test configuration via environment variables"""
        from ip_provider import create_configured_ip_provider

        provider = create_configured_ip_provider()

        # Should be cached but not fallback
        assert isinstance(provider, CachedIPProvider)
        assert provider.cache_duration == 600
        assert isinstance(provider.provider, HttpIPProvider)

    @patch.dict(
        "os.environ", {"IP_ENABLE_CACHE": "false", "IP_ENABLE_FALLBACK": "true"}
    )
    def test_env_no_cache_with_fallback(self):
        """Test disabling cache but enabling fallback"""
        from ip_provider import create_configured_ip_provider

        provider = create_configured_ip_provider()

        # Should be fallback but not cached
        assert isinstance(provider, FallbackIPProvider)
        assert len(provider.providers) == 3
