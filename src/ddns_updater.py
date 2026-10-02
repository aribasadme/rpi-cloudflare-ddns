"""
DNS Updater Module - orchestrates the complete DNS update process
"""

import logging
import os
import time
from dataclasses import dataclass

from cloudflare import Cloudflare

from configuration_manager import (
    AuthenticationConfig,
    CloudflareZoneConfig,
    ConfigurationError,
    ConfigurationManager,
    DDNSConfiguration,
)
from ip_provider import IPProvider, IPProviderError
from record_manager import BatchRecordManager, ZoneUpdateSummary

logger = logging.getLogger(__name__)


class DNSUpdaterError(Exception):
    """Custom exception for DNS updater errors"""


class AuthenticationError(DNSUpdaterError):
    """Exception for authentication-related errors"""


class ZoneValidationError(DNSUpdaterError):
    """Exception for zone validation errors"""


@dataclass
class UpdateCycleResult:
    """Result of a complete update cycle"""

    ip_addresses: dict[str, str]
    ip_changed: bool
    zones_processed: int
    total_records_updated: int
    successful_updates: int
    failed_updates: int
    zone_summaries: list[ZoneUpdateSummary]
    execution_time_seconds: float

    @property
    def success_rate(self) -> float:
        """Calculate overall success rate as percentage"""
        if self.total_records_updated == 0:
            return 100.0
        return (self.successful_updates / self.total_records_updated) * 100

    def _format_ips(self) -> str:
        """Render the per-family addresses, e.g. 'A=1.2.3.4, AAAA=2001:db8::1'"""
        return ", ".join(f"{rtype}={ip}" for rtype, ip in self.ip_addresses.items())

    def __str__(self) -> str:
        if self.ip_changed:
            return (
                f"IP changed to {self._format_ips()}: "
                f"{self.successful_updates}/{self.total_records_updated} "
                f"updates successful ({self.success_rate:.1f}%) in "
                f"{self.execution_time_seconds:.2f}s"
            )
        else:
            return f"No IP change detected: {self._format_ips()}"


class CloudflareAuthenticator:
    """Handles Cloudflare client authentication"""

    @staticmethod
    def create_client(auth_config: AuthenticationConfig) -> Cloudflare:
        """Create Cloudflare client from authentication configuration

        Args:
            auth_config: Authentication configuration

        Returns:
            Configured Cloudflare client

        Raises:
            AuthenticationError: If authentication fails
        """
        try:
            # Try environment variables first (they take precedence)
            api_token = os.getenv("CF_DDNS_API_TOKEN")
            if api_token:
                logger.debug("Using API token from environment variable")
                return Cloudflare(api_token=api_token)

            api_key = os.getenv("CF_DDNS_API_KEY")
            api_email = os.getenv("CF_DDNS_API_EMAIL")
            if api_key and api_email:
                logger.debug("Using API key + email from environment variables")
                return Cloudflare(api_key=api_key, api_email=api_email)

            # Fall back to config file values
            if auth_config.api_token:
                logger.debug("Using API token from configuration file")
                return Cloudflare(api_token=auth_config.api_token)

            elif auth_config.api_key and auth_config.api_email:
                logger.debug("Using API key + email from configuration file")
                return Cloudflare(
                    api_key=auth_config.api_key, api_email=auth_config.api_email
                )

            else:
                raise AuthenticationError(
                    "Invalid authentication configuration. "
                    "Please provide either 'api_token' or both 'api_key' and 'api_email'"
                )

        except Exception as e:
            if isinstance(e, AuthenticationError):
                raise
            raise AuthenticationError(f"Failed to create Cloudflare client: {e}")


class ZoneValidator:
    """Validates and enriches Cloudflare zone configurations"""

    @staticmethod
    def validate_zone(
        cf: Cloudflare, zone_config: CloudflareZoneConfig
    ) -> CloudflareZoneConfig:
        """Validate zone access and enrich configuration with zone data

        Args:
            cf: Cloudflare client
            zone_config: Zone configuration to validate

        Returns:
            Enriched zone configuration with zone_name and client populated

        Raises:
            ZoneValidationError: If zone validation fails
        """
        try:
            zone = cf.zones.get(zone_id=zone_config.zone_id)

            # Create a copy to avoid mutating the original
            validated_config = CloudflareZoneConfig(
                authentication=zone_config.authentication,
                zone_id=zone_config.zone_id,
                subdomains=zone_config.subdomains,
                ttl=zone_config.ttl,
            )

            # Populate runtime fields
            if zone:
                validated_config.zone_name = zone.name
                validated_config.client = cf

                logger.debug(f"Validated zone: {zone.name} ({zone_config.zone_id})")
            return validated_config

        except Exception as e:
            raise ZoneValidationError(
                f"Zone validation failed for {zone_config.zone_id}: {e}"
            )


class DNSUpdater:
    """Main DNS updater that orchestrates the complete update process"""

    def __init__(
        self,
        config_manager: ConfigurationManager,
        ip_providers: dict[str, IPProvider],
        record_manager: BatchRecordManager | None = None,
        authenticator: CloudflareAuthenticator | None = None,
        validator: ZoneValidator | None = None,
    ):
        """Initialize DNS updater

        Args:
            config_manager: Configuration manager
            ip_providers: Mapping of record type ("A"/"AAAA") to its IP provider
            record_manager: Record manager (creates new if None)
            authenticator: Cloudflare authenticator (creates new if None)
            validator: Zone validator (creates new if None)
        """
        self.config_manager = config_manager
        self.ip_providers = ip_providers
        self.record_manager = record_manager or BatchRecordManager()
        self.authenticator = authenticator or CloudflareAuthenticator()
        self.validator = validator or ZoneValidator()

        self._configuration: DDNSConfiguration | None = None
        self._validated_zones: list[CloudflareZoneConfig] = []
        self._last_known_ips: dict[str, str] = {}

    def load_configuration(self) -> DDNSConfiguration:
        """Load and cache configuration

        Returns:
            Loaded configuration

        Raises:
            DNSUpdaterError: If configuration loading fails
        """
        try:
            self._configuration = self.config_manager.load_configuration()
            logger.info(
                f"Loaded configuration with {len(self._configuration.cloudflare_zones)} zones"
            )
            return self._configuration
        except ConfigurationError as e:
            raise DNSUpdaterError(f"Configuration loading failed: {e}")

    def validate_zones(self) -> list[CloudflareZoneConfig]:
        """Validate all configured zones and set up clients

        Returns:
            List of validated zone configurations

        Raises:
            DNSUpdaterError: If no zones can be validated
        """
        if not self._configuration:
            raise DNSUpdaterError(
                "Configuration not loaded. Call load_configuration() first."
            )

        validated_zones: list[CloudflareZoneConfig] = []

        for zone_config in self._configuration.cloudflare_zones:
            try:
                # Create Cloudflare client for this zone
                cf = self.authenticator.create_client(zone_config.authentication)

                # Validate zone and enrich configuration
                validated_zone = self.validator.validate_zone(cf, zone_config)
                validated_zones.append(validated_zone)

                logger.info(
                    f"✓ Validated zone: {validated_zone.zone_name} ({validated_zone.zone_id})"
                )

            except (AuthenticationError, ZoneValidationError) as e:
                logger.warning(f"✗ Failed to validate zone {zone_config.zone_id}: {e}")
                continue
            except Exception as e:
                logger.error(
                    f"Unexpected error validating zone {zone_config.zone_id}: {e}"
                )
                continue

        if not validated_zones:
            raise DNSUpdaterError("No valid zones configured")

        self._validated_zones = validated_zones
        logger.info(f"Successfully validated {len(validated_zones)} zones")
        return validated_zones

    def initialize(self) -> None:
        """Initialize the DNS updater by loading configuration and validating zones

        Raises:
            DNSUpdaterError: If initialization fails
        """
        logger.info("Initializing DNS updater...")

        try:
            self.load_configuration()
            self.validate_zones()
            logger.info("DNS updater initialization completed successfully")
        except DNSUpdaterError:
            raise
        except Exception as e:
            raise DNSUpdaterError(f"DNS updater initialization failed: {e}")

    def _needed_record_types(self) -> set[str]:
        """Record types requested by any subdomain across validated zones"""
        needed: set[str] = set()
        for zone in self._validated_zones:
            for subdomain in zone.subdomains:
                needed.update(subdomain.record_types)
        return needed

    def get_current_ips(self) -> dict[str, str]:
        """Get current public IP address for each needed record type

        Only families requested by at least one subdomain are fetched. IPv4 (A)
        failures raise; IPv6 (AAAA) failures are logged and skipped so IPv4-only
        hosts keep working.

        Returns:
            Mapping of record type to its current public IP address

        Raises:
            DNSUpdaterError: If a required IPv4 address cannot be obtained
        """
        ips: dict[str, str] = {}
        for record_type in self._needed_record_types():
            provider = self.ip_providers.get(record_type)
            if provider is None:
                logger.warning(f"No IP provider configured for {record_type}")
                continue
            try:
                ips[record_type] = provider.get_public_ip()
            except IPProviderError as e:
                if record_type == "A":
                    raise DNSUpdaterError(f"Failed to obtain public IP: {e}")
                logger.warning(
                    f"Could not obtain {record_type} address, skipping it: {e}"
                )
        return ips

    def update_dns_records(self, new_ips: dict[str, str]) -> UpdateCycleResult:
        """Update DNS records with new IP addresses

        Args:
            new_ips: Mapping of record type ("A"/"AAAA") to the new address

        Returns:
            Update cycle result

        Raises:
            DNSUpdaterError: If updates fail completely
        """
        if not self._validated_zones:
            raise DNSUpdaterError(
                "No validated zones available. Call initialize() first."
            )

        start_time = time.time()

        try:
            # Update all zones
            zone_summaries = self.record_manager.update_all_zones(
                self._validated_zones, new_ips
            )

            # Calculate overall statistics
            overall_stats = self.record_manager.get_overall_summary(zone_summaries)

            execution_time = time.time() - start_time

            result = UpdateCycleResult(
                ip_addresses=new_ips,
                ip_changed=new_ips != self._last_known_ips,
                zones_processed=overall_stats["total_zones"],
                total_records_updated=overall_stats["total_records"],
                successful_updates=overall_stats["successful_updates"],
                failed_updates=overall_stats["failed_updates"],
                zone_summaries=zone_summaries,
                execution_time_seconds=execution_time,
            )

            self._last_known_ips = new_ips

            if result.failed_updates > 0:
                logger.warning(
                    f"Update cycle completed with {result.failed_updates} failures: {result}"
                )
            else:
                logger.info(f"Update cycle completed successfully: {result}")

            return result

        except Exception as e:
            raise DNSUpdaterError(f"DNS record update failed: {e}")

    def check_and_update(self) -> UpdateCycleResult:
        """Check current IP and update DNS records if changed

        Returns:
            Update cycle result

        Raises:
            DNSUpdaterError: If the check and update process fails
        """
        try:
            # Get current IPs
            current_ips = self.get_current_ips()

            # Check if any IP changed
            if current_ips != self._last_known_ips:
                logger.info(
                    f"Public IP changed from {self._last_known_ips} to {current_ips}"
                )
                return self.update_dns_records(current_ips)
            else:
                logger.debug(f"No IP change detected. Current IPs: {current_ips}")

                # Return a "no change" result
                return UpdateCycleResult(
                    ip_addresses=current_ips,
                    ip_changed=False,
                    zones_processed=0,
                    total_records_updated=0,
                    successful_updates=0,
                    failed_updates=0,
                    zone_summaries=[],
                    execution_time_seconds=0.0,
                )

        except DNSUpdaterError:
            raise
        except Exception as e:
            raise DNSUpdaterError(f"Check and update failed: {e}")

    def run_continuous(self, check_interval: int = 900) -> None:
        """Run continuous DNS monitoring and updating

        Args:
            check_interval: Interval between checks in seconds (default: 900 = 15 minutes)

        Raises:
            DNSUpdaterError: If continuous operation fails to start
        """
        if not self._validated_zones:
            raise DNSUpdaterError(
                "DNS updater not initialized. Call initialize() first."
            )

        logger.info(
            f"Starting continuous DNS monitoring every {check_interval} seconds"
        )

        try:
            while True:
                try:
                    result = self.check_and_update()

                    if result.ip_changed and result.failed_updates > 0:
                        logger.warning(f"Some updates failed: {result}")
                    elif result.ip_changed:
                        logger.info(f"DNS update successful: {result}")

                    time.sleep(check_interval)

                except KeyboardInterrupt:
                    logger.info("DNS monitoring stopped by user")
                    break
                except DNSUpdaterError as e:
                    logger.error(f"DNS update error: {e}")
                    logger.info(f"Retrying in {check_interval} seconds...")
                    time.sleep(check_interval)
                except Exception as e:
                    logger.error(f"Unexpected error in monitoring loop: {e}")
                    logger.info(f"Retrying in {check_interval} seconds...")
                    time.sleep(check_interval)

        except Exception as e:
            raise DNSUpdaterError(f"Continuous monitoring failed to start: {e}")


def setup_logging(log_level=logging.INFO):
    """Configures application logging with standard format and handlers."""
    logging.getLogger().setLevel(logging.WARNING)
    logger.setLevel(log_level)
    logger.handlers.clear()
    log_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    logger.addHandler(console_handler)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("cloudflare").setLevel(logging.WARNING)
    return logger


def main():
    """Application entry point with error handling."""
    from dotenv import load_dotenv

    load_dotenv()
    logger = setup_logging()

    logger.info("rpi-cloudflare-ddns starting...")

    try:
        # Create configuration manager
        from configuration_manager import create_configuration_manager

        config_manager = create_configuration_manager()

        # Create IP providers (per record type)
        from ip_provider import create_configured_ip_provider

        ip_providers = create_configured_ip_provider()

        # Create batch record manager
        from record_manager import create_batch_record_manager

        batch_manager = create_batch_record_manager()

        # Create updater
        updater = DNSUpdater(
            config_manager=config_manager,
            ip_providers=ip_providers,
            record_manager=batch_manager,
        )

        # Initialize updater (load config, validate zones)
        updater.initialize()

        # Get check interval from env or default
        check_interval = int(os.environ.get("CHECK_INTERVAL", 900))
        logger.info(f"Starting periodic checks every {check_interval} seconds")

        # Run continuous update loop
        updater.run_continuous(check_interval=check_interval)
        return 0

    except KeyboardInterrupt:
        logger.info("Application stopped by user")
        return 0
    except DNSUpdaterError as e:
        logger.error(f"DNSUpdater error: {e}")
        return 1
    except Exception as e:
        logger.error(f"Application error: {e}")
        return 1


def validate_config_command():
    """Simple wrapper that calls the validation from configuration_manager"""
    from configuration_manager import validate_configuration_command

    return validate_configuration_command()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--validate":
        sys.exit(validate_config_command())
    else:
        sys.exit(main())
