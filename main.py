__version__ = "1.0.0"

import json
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
from typing import Dict, List, Optional

from cloudflare import Cloudflare
from cloudflare.types.dns import AAAARecord, ARecord
from dotenv import load_dotenv

logger = logging.getLogger("ddns_updater")

load_dotenv()

ENV_VARS = {
    key: value for (key, value) in os.environ.items() if key.startswith("CF_DDNS_")
}

BASE_PATH = os.getcwd()


@dataclass
class DnsUpdateRequest:
    zone_id: str
    fqdn: str
    record_id: str
    record_type: str
    proxied: bool
    content: str


def setup_logging(log_level=logging.INFO) -> None:
    """
    Configure logging to stdout/stderr.
    Should be called at the start of the application.
    """
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


def load_configuration():
    """
    Loads configuration with better error handling and validation
    """
    try:
        config_path = os.path.join(BASE_PATH, "config.json")
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"Configuration file not found at {config_path}")

        with open(config_path, "r") as config_file:
            config = json.loads(Template(config_file.read()).safe_substitute(ENV_VARS))

            # Validate required fields
            required_fields = ["cloudflare"]
            for field in required_fields:
                if field not in config:
                    raise ValueError(f"Missing required field: {field}")

            return config
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in config file: {str(e)}")
        raise


def get_public_ip(timeout: int = 5) -> Optional[str]:
    """
    Retrieves the public IP address of the machine.

    Returns:
        str: The public IP address

    Raises:
        URLError: If the connection to the IP service fails
        TimeoutError: If the request times out

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


def fetch_records(cf: Cloudflare, zone_id: str) -> list[Dict]:
    """
    Fetches all DNS records for the specified zone.

    Args:
        cf (Cloudflare): An instance of the Cloudflare class
        zone_id (str): The ID of the Cloudflare zone

    Returns:
        list: A list of only A or AAAA records
    """
    try:
        records = cf.dns.records.list(zone_id=zone_id)
        for record in records:
            logger.debug(f"Record: {record}")
        return [
            record for record in records if isinstance(record, (ARecord, AAAARecord))
        ]
    except Exception as e:
        logger.error(f"Error fetching records for zone {zone_id}: {str(e)}")
        return []


def prepare_updates(
    config: dict, records: List[Dict], ip: str
) -> List[DnsUpdateRequest]:
    """
    Prepares a list of records that need to be updated.

    Args:
        config (dict):
        records (List[Dict]): List of records that need to be updated
        new_ip (str): IP address for all records

    Returns:
        List[DnsUpdateRequest]:
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
    """
    Updates the DNS records for the specified domain.

    Args:
        cf (Cloudflare): An instance of the Cloudflare class
        records (list): A list of DNS records to update
        cf_config (dict): Options to be updated
        ip (str): The new IP address to set for the DNS records
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
                        f"Updated {update.fqdn} from {update.content} to {ip} (type: {update.record_type}, proxied: {update.proxied}, ttl: {ttl})"
                    )
                except Exception as e:
                    logger.error(f"Failed to update {update.fqdn}: {str(e)}")
                    continue

        except Exception as e:
            logger.error(f"Error processing zone {zone_id}: {str(e)}")
            continue


def run() -> None:
    """
    Runs the DNS update check periodically
    """
    try:
        config = load_configuration()
        ttl = int(config.get("ttl", 300))
        check_interval = os.environ.get("CHECK_INTERVAL", 900)
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
                    for cf_config in config["cloudflare"]:
                        try:
                            api_token = cf_config["authentication"]["api_token"]
                            cf = Cloudflare(api_token=api_token)

                            zone_id = cf_config["zone_id"]

                            zone = cf.zones.get(zone_id=zone_id)
                            if zone is None:
                                logger.error(f"Zone not found: {zone_id}")
                                continue
                            cf_config["zone_name"] = zone.name

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

                time.sleep(check_interval)

            except Exception as e:
                logger.error(f"Error in check cycle: {str(e)}")
                time.sleep(check_interval)

    except Exception as e:
        logger.error(f"Application error: {str(e)}")
        return 1


def main():
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
