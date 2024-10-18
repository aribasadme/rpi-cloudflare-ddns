import logging
import os
import urllib.request
import sys

from datetime import datetime
from dotenv import load_dotenv
from cloudflare import Cloudflare
from cloudflare.types.dns import ARecord, CNAMERecord


logger = logging.getLogger()
logger.setLevel(logging.INFO)

log_file = os.path.join(os.path.dirname(__file__), "py_logs.log")
log_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
file_handler.setFormatter(log_formatter)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)

logger.addHandler(file_handler)
logger.addHandler(console_handler)

load_dotenv()

ip_file = os.path.join(os.path.dirname(__file__), "ip.txt")


def get_public_ip() -> str:
    """
    Retrieves the external IP address of the machine.

    Returns:
        str: The external IP address
    """
    try:
        url = "https://api.ipify.org"  # IPv4 only
        response = urllib.request.urlopen(url, timeout=5)
        external_ip = response.read().decode("utf-8")
        logger.info(f"Current IP: {external_ip}")
        return external_ip
    except Exception as e:
        logger.error(f"Error: {e.reason.strerror}")
        return


def ip_has_changed() -> tuple[bool, str]:
    """
    Checks if the external IP address has changed since the last run and
    updates it.

    Returns:
        bool: True and the new IP address if the IP address has changed,
              False otherwise
    """
    try:
        with open(ip_file, "r") as f:
            old_ip = f.read().strip()
            logger.info(f"Old IP: {old_ip}")
        new_ip = get_public_ip()
        if old_ip == new_ip:
            logger.info("IP address has not changed")
            return False, None
        with open(ip_file, "w") as f:
            f.write(new_ip)
        logger.info("Saving new IP address to cache")
        return True, new_ip
    except Exception as e:
        logger.error(f"Error: {e.reason.strerror}")
        return False, None


def fetch_dns_records(cf: Cloudflare, zone_id: str, type: str = "A") -> list:
    """
    Fetches the DNS records for the specified zone.

    Args:
        cf (Cloudflare): An instance of the Cloudflare class
        zone_id (str): The ID of the Cloudflare zone
        record_type (str): The type of DNS records to fetch ('A' or 'CNAME',
            defaults to 'ALL')

    Returns:
        list: A list of DNS records
    """
    record_types = {
        "ALL": lambda record: True,
        "A": lambda record: isinstance(record, ARecord),
        "CNAME": lambda record: isinstance(record, CNAMERecord),
    }
    filter_func = record_types.get(type, None)
    if filter_func is None:
        raise ValueError(f"Invalid record type: {type}")

    records = cf.dns.records.list(zone_id=zone_id)
    logger.info(f"DNS records fetched: {len(list(records))}")

    logger.info(f"Filtering records by type: {type}")
    filtered_records = [record for record in records if filter_func(record)]
    logging.debug(f"Filtered records: {filtered_records}")
    logger.info(f"DNS records filtered: {len(filtered_records)}")

    return filtered_records


def update_dns_records(cf: Cloudflare, records: list, zone_id: str, ip: str):
    """
    Updates the DNS records for the specified domain.

    Args:
        cf (Cloudflare): An instance of the Cloudflare class
        records (list): A list of DNS records to update
        zone_id (str): The ID of the Cloudflare zone
        ip (str): The new IP address to set for the DNS records
    """
    for record in records:
        logging.debug(f"Updating record: {record}")
        if record.content == ip:
            logger.info(f"No changes needed for {record.name}")
            continue
        try:
            cf.dns.records.update(
                dns_record_id=record.id,
                zone_id=zone_id,
                content=ip,
                name=record.name,
                type=record.type,
                ttl=record.ttl,
                comment=f"Updated by rpi-cloudflare-ddns on {datetime.now()}",
            )
        except Exception as e:
            logger.error(f"Error: {e.reason.strerror}")


def main():
    has_changed, ip = ip_has_changed()
    if has_changed:
        cf = Cloudflare()
        dns_records = fetch_dns_records(cf, os.getenv("ZONE_ID"), type="A")
        update_dns_records(cf, dns_records, os.getenv("ZONE_ID"), ip=ip)
        logger.info("DNS records updated successfully")


if __name__ == "__main__":
    main()
