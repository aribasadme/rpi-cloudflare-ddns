"""
IP Provider module - handles fetching public IP addresses
"""

import ipaddress
import logging
import urllib.request
from abc import ABC, abstractmethod
from urllib.error import URLError

logger = logging.getLogger(__name__)

# Endpoints per address family. Kept in separate lists so a fallback chain
# never mixes IPv4 and IPv6 sources.
_IPV4_ENDPOINTS = [
    "https://api.ipify.org",
    "https://checkip.amazonaws.com",
    "https://icanhazip.com",
]
_IPV6_ENDPOINTS = [
    "https://api6.ipify.org",
    "https://ipv6.icanhazip.com",
]


class IPProviderError(Exception):
    """Custom exception for IP provider related errors"""


class IPProvider(ABC):
    """Abstract base class for IP providers"""

    @abstractmethod
    def get_public_ip(self) -> str:
        """Get public IP address. Raises IPProviderError on failure."""


class HttpIPProvider(IPProvider):
    """HTTP-based IP provider using external services"""

    def __init__(
        self, url: str = "https://api.ipify.org", timeout: int = 5, family: int = 4
    ):
        if family not in (4, 6):
            raise ValueError(f"family must be 4 or 6, got {family}")
        self.url = url
        self.timeout = timeout
        self.family = family

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
        """Validate that the address parses and matches the expected family"""
        try:
            return ipaddress.ip_address(ip).version == self.family
        except ValueError:
            return False


class CachedIPProvider(IPProvider):
    """Decorator that adds caching to any IP provider"""

    def __init__(self, provider: IPProvider, cache_duration: int = 300):
        self.provider = provider
        self.cache_duration = cache_duration
        self._cached_ip: str | None = None
        self._cache_time: float | None = None

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
    with_cache: bool = True,
    with_fallback: bool = True,
    cache_duration: int = 300,
    family: str = "ipv4",
) -> IPProvider:
    """Create a production-ready IP provider with common configurations

    Args:
        family: "ipv4" (A records) or "ipv6" (AAAA records)
    """
    if family == "ipv6":
        endpoints, ip_family = _IPV6_ENDPOINTS, 6
    elif family == "ipv4":
        endpoints, ip_family = _IPV4_ENDPOINTS, 4
    else:
        raise ValueError(f"family must be 'ipv4' or 'ipv6', got {family!r}")

    providers: list[IPProvider] = [
        HttpIPProvider(url, timeout=5, family=ip_family) for url in endpoints
    ]

    if with_fallback:
        provider: IPProvider = FallbackIPProvider(providers)
    else:
        provider = providers[0]  # Just use the primary

    if with_cache:
        provider = CachedIPProvider(provider, cache_duration=cache_duration)

    return provider


# Add IP provider configuration via environment variables
def create_configured_ip_provider() -> dict[str, IPProvider]:
    """Create IP providers per record type based on environment configuration

    Returns:
        Mapping of record type ("A"/"AAAA") to its IP provider.
    """
    import os

    cache_duration = int(os.environ.get("IP_CACHE_DURATION", "300"))
    enable_fallback = os.environ.get("IP_ENABLE_FALLBACK", "true").lower() == "true"
    enable_cache = os.environ.get("IP_ENABLE_CACHE", "true").lower() == "true"

    logger.info(
        f"IP Provider config - Cache: {enable_cache} ({cache_duration}s), "
        f"Fallback: {enable_fallback}"
    )
    return {
        "A": create_ip_provider(
            with_cache=enable_cache,
            with_fallback=enable_fallback,
            cache_duration=cache_duration,
            family="ipv4",
        ),
        "AAAA": create_ip_provider(
            with_cache=enable_cache,
            with_fallback=enable_fallback,
            cache_duration=cache_duration,
            family="ipv6",
        ),
    }


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
