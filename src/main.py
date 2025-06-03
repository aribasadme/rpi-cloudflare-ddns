__version__ = "2.0.0"

import logging
import os
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby
from operator import attrgetter
from string import Template
from typing import Any, Dict, List, Optional

import yaml
from cloudflare import Cloudflare
from cloudflare._exceptions import NotFoundError
from cloudflare.types.dns import ARecord
from dotenv import load_dotenv
from schema import And, Or, Schema, SchemaError, Use
from schema import Optional as SchemaOptional

logger = logging.getLogger("ddns_updater")

load_dotenv()

ENV_VARS = {
    key: value for (key, value) in os.environ.items() if key.startswith("CF_DDNS_")
}

BASE_PATH = os.getcwd()

CONFIG_SCHEMA = Schema(
    {
        "cloudflare": [
            {
                "authentication": Or(
                    {"api_token": str},
                    {"api_key": str, "api_email": str},
                ),
                "zone_id": str,
                "subdomains": [{"name": str, SchemaOptional("proxied"): bool}],
            }
        ],
        SchemaOptional("ttl"): And(Use(int), lambda n: 60 < n <= 86400),
    }
)


@dataclass
class DnsUpdateRequest:
    zone_id: str
    fqdn: str
    record_id: str
    record_type: str
    proxied: bool
    content: str


def setup_logging(log_level=logging.INFO) -> None:
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


def load_configuration() -> Dict[str, Any]:
    """Loads and validates configuration from YAML file.
    
    Raises:
        FileNotFoundError: Config file not found
        yaml.YAMLError: Invalid YAML syntax
        SchemaError: Invalid configuration schema
    """
    config_extensions = ["yaml", "yml"]
    config_path = None

    for ext in config_extensions:
        path = os.path.join(BASE_PATH, f"config.{ext}")
        if os.path.exists(path):
            config_path = path
            break

    if not config_path:
        raise FileNotFoundError(
            f"Configuration file not found. Tried: {', '.join(f'config.{ext}' for ext in config_extensions)}"
        )

    try:
        with open(config_path, "r") as config_file:
            config = yaml.safe_load(
                Template(config_file.read()).safe_substitute(ENV_VARS)
            )

            CONFIG_SCHEMA.validate(config)

            logger.info(f"Loaded configuration from {config_path}")
            return config
    except yaml.YAMLError as e:
        logger.error(f"Invalid YAML in config file: {str(e)}")
        raise
    except SchemaError as e:
        logger.error(f"Configuration validation failed: {str(e)}")
        raise


def validate_configuration(config: dict) -> list[dict]:
    """
    Validates the Cloudflare configuration by checking zone access
    Returns a list of valid configurations
    """
    valid_configs = []

    try:
        for cf_config in config["cloudflare"]:
            try:
                auth_config = cf_config.get("authentication", {})
                cf = get_cloudflare_client(auth_config)
                zone_id = cf_config["zone_id"]

                # Try to fetch the zone - this will fail if zone_id is invalid
                # or if we don't have proper access
                try:
                    zone = cf.zones.get(zone_id=zone_id)

                    cf_config["zone_name"] = zone.name
                    cf_config["client"] = cf

                    valid_configs.append(cf_config)
                    logger.info(f"Successfully validated zone: {zone.name} ({zone_id})")

                except NotFoundError:
                    logger.error(f"Zone not found: {zone_id}")
                    continue

            except Exception as e:
                logger.error(
                    f"Failed to validate zone '{cf_config.get('zone_id')}': {str(e)}"
                )
                continue

        if not valid_configs:
            logger.error("No valid zones found in configuration")
        else:
            logger.info(f"Found {len(valid_configs)} valid zone(s)")

        return valid_configs

    except Exception as e:
        logger.error(f"Configuration validation failed: {str(e)}")
        return []


def get_public_ip(timeout: int = 5) -> Optional[str]:
    """Gets machine's public IP address from ipify.org.
    
    Returns:
        Optional[str]: Public IP address or None if request fails
    """
    public_ip = None
    try:
        url = "https://api.ipify.org"  # IPv4 only
        response = urllib.request.urlopen(url, timeout=timeout)
        public_ip = response.read().decode("utf-8")
        logger.info(f"Public IP: {public_ip}")
    except urllib.error.URLError as e:
        logger.error(f"Connection error: {e}")
    except TimeoutError:
        logger.error(f"Request timed out after {timeout} seconds")
    finally:
        return public_ip


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


def fetch_records(cf: Cloudflare, zone_id: str) -> list[Dict]:
    """Fetches A records for the specified Cloudflare zone.
    
    Returns:
        list[Dict]: List of A records or empty list on error
    """
    try:
        records = cf.dns.records.list(zone_id=zone_id)
        for record in records:
            logger.debug(f"Record: {record}")
        return [record for record in records if isinstance(record, ARecord)]
    except Exception as e:
        logger.error(f"Error fetching records for zone {zone_id}: {str(e)}")
        return []


def prepare_updates(
    config: dict, records: List[Dict], ip: str
) -> List[DnsUpdateRequest]:
    """Identifies DNS records that need IP address updates.
    
    Returns:
        List[DnsUpdateRequest]: Records requiring updates
    """
    updates: List[DnsUpdateRequest] = []

    zone_id = config["zone_id"]
    base_domain = config["zone_name"]

    record_map: Dict[str, Dict] = {}
    for record in records:
        record_map[record.name.lower()] = record

    for subdomain in config["subdomains"]:
        name = subdomain["name"].lower().strip()
        proxied = subdomain["proxied"]

        fqdn = base_domain
        if name != "" and name != "@":
            fqdn = f"{name}.{base_domain}"

        if record := record_map.get(fqdn):
            if record.content != ip:
                updates.append(
                    DnsUpdateRequest(
                        zone_id=zone_id,
                        fqdn=fqdn,
                        record_id=record.id,
                        record_type=record.type,
                        proxied=proxied,
                        content=record.content,
                    )
                )

    return updates


def update_records(cf: Cloudflare, updates: List[DnsUpdateRequest], ip: str, ttl: int):
    """Updates DNS records with new IP address.
    
    Handles updates per zone, logging success and failures.
    """
    for zone_id, zone_updates in groupby(updates, key=attrgetter("zone_id")):
        zone_updates = list(zone_updates)

        try:
            for update in zone_updates:
                try:
                    cf.dns.records.update(
                        dns_record_id=update.record_id,
                        zone_id=zone_id,
                        content=ip,
                        name=update.fqdn,
                        type=update.record_type,
                        proxied=update.proxied,
                        ttl=ttl,
                        comment=f"Updated by rpi-cloudflare-ddns on {datetime.now()}",
                    )
                    logger.info(f"Updated {update.fqdn} from {update.content} to {ip}")
                    logger.debug(
                        f"Updated {update.fqdn} from {update.content} to {ip} "
                        f"(type: {update.record_type}, proxied: {update.proxied}, ttl: {ttl})"
                    )
                except Exception as e:
                    logger.error(f"Failed to update {update.fqdn}: {str(e)}")
                    continue

        except Exception as e:
            logger.error(f"Error processing zone {zone_id}: {str(e)}")
            continue


def run() -> None:
    """Main update loop that monitors IP changes and updates DNS records."""
    try:
        config = load_configuration()

        valid_configs = validate_configuration(config, get_cloudflare_client(config["cloudflare"][0]["authentication"]))
        if not valid_configs:
            logger.error("No valid configurations found, exiting...")
            return 1

        ttl = int(config.get("ttl", 300))
        check_interval = int(os.environ.get("CHECK_INTERVAL", 900))
        logger.info(f"Starting periodic checks every {check_interval} seconds")

        last_known_ip: Optional[str] = None

        while True:
            try:
                ip = get_public_ip()
                if not ip:
                    logger.error("Failed to obtain public IP")
                    time.sleep(check_interval)
                    continue

                if ip != last_known_ip:
                    logger.info(f"Public IP changed from {last_known_ip} to {ip}")
                    for cf_config in valid_configs:
                        try:
                            cf = cf_config["client"]
                            zone_id = cf_config["zone_id"]

                            records = fetch_records(cf, zone_id)
                            updates = prepare_updates(cf_config, records, ip)
                            if updates:
                                update_records(cf, updates, ip, ttl)
                            else:
                                logger.info("No records need updating")

                        except Exception as e:
                            logger.error(f"Error processing configuration: {str(e)}")
                            continue

                    last_known_ip = ip
                else:
                    logger.info(f"No IP change detected. Current IP: {ip}")

                logger.info("Sleeping...")
                time.sleep(check_interval)

            except Exception as e:
                logger.error(f"Error in check cycle: {str(e)}")
                time.sleep(check_interval)

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
        run()
        return 0
    except KeyboardInterrupt:
        logger.info("Application stopped by user")
        return 0
    except Exception as e:
        logger.error(f"Application error: {str(e)}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
