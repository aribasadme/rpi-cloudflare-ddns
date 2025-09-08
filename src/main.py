__version__ = "2.3.0"

import logging
import os
import sys
import time
from typing import Dict, Optional

from cloudflare import Cloudflare
from dotenv import load_dotenv

from configuration_manager import (
    CloudflareZoneConfig,
    ConfigurationError,
    create_configuration_manager,
)
from ip_provider import IPProviderError, create_configured_ip_provider
from record_manager import RecordManagerError, create_batch_record_manager

logger = logging.getLogger("ddns_updater")

load_dotenv()

ENV_VARS = {
    key: value for (key, value) in os.environ.items() if key.startswith("CF_DDNS_")
}

BASE_PATH = os.getcwd()


def setup_logging(log_level=logging.INFO):
    """Configures application logging with standard format and handlers."""
    logging.getLogger().setLevel(logging.WARNING)

    logger.setLevel(log_level)

    logger.handlers.clear()

    log_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)

    logger.addHandler(console_handler)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("cloudflare").setLevel(logging.WARNING)

    return logger


def get_cloudflare_client(auth_config: dict) -> Cloudflare:
    """Creates Cloudflare client from environment variables or config.

    Raises:
        ValueError: Invalid authentication configuration
    """
    # First try environment variables
    api_token = os.getenv("CF_DDNS_API_TOKEN")
    if api_token:
        return Cloudflare(api_token=api_token)

    api_key = os.getenv("CF_DDNS_API_KEY")
    api_email = os.getenv("CF_DDNS_API_EMAIL")
    if api_key and api_email:
        return Cloudflare(api_key=api_key, api_email=api_email)

    # Fall back to config file if environment variables are not set
    if "api_token" in auth_config:
        return Cloudflare(api_token=auth_config["api_token"])
    elif "api_key" in auth_config and "api_email" in auth_config:
        return Cloudflare(
            api_key=auth_config["api_key"], api_email=auth_config["api_email"]
        )
    else:
        raise ValueError(
            "Invalid authentication configuration. "
            "Please provide either 'api_token' or both 'api_key' and 'api_email'"
        )


def _auth_config_to_dict(auth_config) -> Dict[str, Optional[str]]:
    """Convert AuthenticationConfig dataclass to dict for existing functions"""
    return {
        "api_token": auth_config.api_token,
        "api_key": auth_config.api_key,
        "api_email": auth_config.api_email,
    }


def validate_configuration(
    cf, zone_config: CloudflareZoneConfig
) -> Optional[CloudflareZoneConfig]:
    """Validates Cloudflare zone access and enriches config with zone data.

    Args:
        cf: Cloudflare client
        zone_config: CloudflareZoneConfig dataclass

    Returns:
        CloudflareZoneConfig with populated zone_name and client, or None if validation fails
    """
    try:
        zone = cf.zones.get(zone_id=zone_config.zone_id)

        # Populate runtime fields
        zone_config.zone_name = zone.name
        zone_config.client = cf

        return zone_config

    except Exception as e:
        logger.error(f"Zone validation failed for {zone_config.zone_id}: {e}")
        return None


def run() -> int:
    """Main update loop that monitors IP changes and updates DNS records.

    Returns:
        int: 0 on success, 1 on error
    """
    try:
        logger.info("Loading configuration...")
        config_manager = create_configuration_manager()

        try:
            config = config_manager.load_configuration()
        except ConfigurationError as e:
            logger.error(f"Configuration error: {e}")
            return 1

        logger.info("Validating Cloudflare zones...")
        valid_zones = []

        for zone_config in config.cloudflare_zones:
            try:
                # Convert authentication dataclass back to dict for existing function
                auth_dict = _auth_config_to_dict(zone_config.authentication)
                cf = get_cloudflare_client(auth_dict)

                # Validate the zone (this populates zone_name and client)
                validated_zone = validate_configuration(cf, zone_config)
                if validated_zone:
                    valid_zones.append(validated_zone)
                    logger.info(
                        f"✓ Validated zone: {validated_zone.zone_name} ({validated_zone.zone_id})"
                    )
                else:
                    logger.warning(f"✗ Failed to validate zone: {zone_config.zone_id}")
            except Exception as e:
                logger.error(f"Error setting up zone {zone_config.zone_id}: {e}")
                continue

        if not valid_zones:
            logger.error("No valid zones configured, exiting...")
            return 1

        logger.info(f"Successfully configured {len(valid_zones)} zones")

        logger.info("Initializing IP provider...")
        ip_provider = create_configured_ip_provider()

        logger.info("Initializing batch record manager...")
        batch_manager = create_batch_record_manager()

        check_interval = int(os.environ.get("CHECK_INTERVAL", 900))
        logger.info(f"Starting periodic checks every {check_interval} seconds")

        last_known_ip: Optional[str] = None

        while True:
            try:
                # Get current IP
                try:
                    ip = ip_provider.get_public_ip()
                except IPProviderError as e:
                    logger.error(f"Failed to obtain public IP: {e}")
                    time.sleep(check_interval)
                    continue

                # Check if IP changed
                if ip != last_known_ip:
                    logger.info(f"Public IP changed from {last_known_ip} to {ip}")

                    try:
                        # Use batch manager to update all zones
                        summaries = batch_manager.update_all_zones(valid_zones, ip)

                        # Log results
                        overall_stats = batch_manager.get_overall_summary(summaries)

                        logger.info(
                            f"Update completed: {overall_stats['successful_updates']}/{overall_stats['total_records']} "
                            f"records updated across {overall_stats['successful_zones']}/{overall_stats['total_zones']} zones"
                        )

                        # Log any failures
                        for summary in summaries:
                            if summary.failed_updates > 0:
                                logger.warning(
                                    f"Zone {summary.zone_name}: {summary.failed_updates} updates failed"
                                )
                                for result in summary.results:
                                    if not result.success:
                                        logger.error(f"  Failed: {result}")

                        # Log successful updates at debug level
                        for summary in summaries:
                            for result in summary.results:
                                if result.success:
                                    logger.debug(f"  Success: {result}")

                    except RecordManagerError as e:
                        logger.error(f"Record management error: {e}")
                    except Exception as e:
                        logger.error(f"Unexpected error during DNS updates: {e}")

                    last_known_ip = ip
                else:
                    logger.debug(f"No IP change detected. Current IP: {ip}")

                time.sleep(check_interval)

            except KeyboardInterrupt:
                logger.info("Application stopped by user")
                break
            except Exception as e:
                logger.error(f"Error in check cycle: {str(e)}")
                time.sleep(check_interval)

        return 0

    except Exception as e:
        logger.error(f"Application error: {str(e)}")
        return 1


def main():
    """Application entry point with error handling.

    Returns:
        int: Exit code (0: success, 1: error)
    """
    try:
        logger = setup_logging()

        logger.info(f"rpi-cloudflare-ddns version {__version__} starting...")

        result = run()
        return result

    except KeyboardInterrupt:
        logger.info("Application stopped by user")
        return 0
    except Exception as e:
        logger.error(f"Application error: {str(e)}")
        return 1


def validate_config_command():
    """Simple wrapper that calls the validation from configuration_manager"""
    from configuration_manager import validate_configuration_command

    return validate_configuration_command()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--validate":
        sys.exit(validate_config_command())
    else:
        sys.exit(main())
