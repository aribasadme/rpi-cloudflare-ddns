"""
IP Provider module - handles fetching public IP addresses
"""

import logging
import urllib.request
from abc import ABC, abstractmethod
from typing import Optional
from urllib.error import URLError

logger = logging.getLogger(__name__)


class IPProviderError(Exception):
    """Custom exception for IP provider related errors"""

    pass


class IPProvider(ABC):
    """Abstract base class for IP providers"""

    @abstractmethod
    def get_public_ip(self) -> str:
        """Get public IP address. Raises IPProviderError on failure."""
        pass


class HttpIPProvider(IPProvider):
    """HTTP-based IP provider using external services"""

    def __init__(self, url: str = "https://api.ipify.org", timeout: int = 5):
        self.url = url
        self.timeout = timeout

    def get_public_ip(self) -> str:
        """Gets machine's public IP address from HTTP service.

        Returns:
            str: Public IP address

        Raises:
            IPProviderError: If unable to fetch IP address
        """
        try:
            response = urllib.request.urlopen(self.url, timeout=self.timeout)
            ip = response.read().decode("utf-8").strip()

            if not self._is_valid_ip(ip):
                raise IPProviderError(f"Invalid IP format received: {ip}")

            logger.info(f"Retrieved public IP: {ip}")
            return ip

        except URLError as e:
            raise IPProviderError(f"Network error while fetching IP: {e}")
        except UnicodeDecodeError as e:
            raise IPProviderError(f"Invalid response format: {e}")
        except Exception as e:
            raise IPProviderError(f"Unexpected error fetching IP: {e}")

    def _is_valid_ip(self, ip: str) -> bool:
        """Basic IPv4 validation"""
        parts = ip.split(".")
        if len(parts) != 4:
            return False
        try:
            return all(0 <= int(part) <= 255 for part in parts)
        except ValueError:
            return False


class CachedIPProvider(IPProvider):
    """Decorator that adds caching to any IP provider"""

    def __init__(self, provider: IPProvider, cache_duration: int = 300):
        self.provider = provider
        self.cache_duration = cache_duration
        self._cached_ip: Optional[str] = None
        self._cache_time: Optional[float] = None

    def get_public_ip(self) -> str:
        """Get IP with caching support"""
        import time

        current_time = time.time()

        # Return cached IP if still valid
        if (
            self._cached_ip
            and self._cache_time
            and current_time - self._cache_time < self.cache_duration
        ):
            logger.debug(f"Using cached IP: {self._cached_ip}")
            return self._cached_ip

        # Fetch new IP
        try:
            ip = self.provider.get_public_ip()
            self._cached_ip = ip
            self._cache_time = current_time
            return ip
        except IPProviderError:
            # If fetch fails and we have a cached IP, use it
            if self._cached_ip:
                logger.warning("Using stale cached IP due to fetch failure")
                return self._cached_ip
            raise


class FallbackIPProvider(IPProvider):
    """IP provider with multiple fallback sources"""

    def __init__(self, providers: list[IPProvider]):
        if not providers:
            raise ValueError("At least one provider must be specified")
        self.providers = providers

    def get_public_ip(self) -> str:
        """Try providers in order until one succeeds"""
        errors = []

        for i, provider in enumerate(self.providers):
            try:
                return provider.get_public_ip()
            except IPProviderError as e:
                errors.append(f"Provider {i}: {e}")
                logger.debug(f"Provider {i} failed: {e}")
                continue

        # All providers failed
        error_summary = "; ".join(errors)
        raise IPProviderError(f"All IP providers failed: {error_summary}")


# Factory function for easy setup
def create_ip_provider(
    with_cache: bool = True, with_fallback: bool = True, cache_duration: int = 300
) -> IPProvider:
    """Create a production-ready IP provider with common configurations"""

    # Primary and fallback providers
    providers: list[IPProvider] = [
        HttpIPProvider("https://api.ipify.org", timeout=5),
        HttpIPProvider("https://checkip.amazonaws.com", timeout=5),
        HttpIPProvider("https://icanhazip.com", timeout=5),
    ]

    if with_fallback:
        provider = FallbackIPProvider(providers)
    else:
        provider = providers[0]  # Just use the primary

    if with_cache:
        provider = CachedIPProvider(provider, cache_duration=cache_duration)

    return provider


# Add IP provider configuration via environment variables
def create_configured_ip_provider():
    """Create IP provider based on environment configuration"""
    import os

    cache_duration = int(os.environ.get("IP_CACHE_DURATION", "300"))
    enable_fallback = os.environ.get("IP_ENABLE_FALLBACK", "true").lower() == "true"
    enable_cache = os.environ.get("IP_ENABLE_CACHE", "true").lower() == "true"

    logger.info(
        f"IP Provider config - Cache: {enable_cache} ({cache_duration}s), "
        f"Fallback: {enable_fallback}"
    )
    return create_ip_provider(
        with_cache=enable_cache,
        with_fallback=enable_fallback,
        cache_duration=cache_duration,
    )


# Example usage and migration guide:
if __name__ == "__main__":
    # Basic usage
    provider = create_ip_provider()

    try:
        ip = provider.get_public_ip()
        print(f"Current IP: {ip}")
    except IPProviderError as e:
        print(f"Failed to get IP: {e}")

    # Custom configuration
    custom_provider = CachedIPProvider(
        FallbackIPProvider(
            [
                HttpIPProvider("https://api.ipify.org"),
                HttpIPProvider("https://icanhazip.com"),
            ]
        ),
        cache_duration=600,  # 10 minutes
    )
