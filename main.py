import logging
import os
import urllib.request

from datetime import datetime
from dotenv import load_dotenv
from cloudflare import Cloudflare
from cloudflare.types.dns import ARecord, CNAMERecord

log_file = os.path.join(os.path.dirname(__file__), 'py_logs.log')
logging.basicConfig(
    filename=log_file,
    filemode="w",
    encoding='utf-8',
    format='%(asctime)s [%(levelname)s] %(message)s',
    level=logging.INFO
)

load_dotenv()


def get_public_ip() -> str:
    """
    Retrieves the external IP address of the machine.

    Returns:
        str: The external IP address
    """
    try:
        url = "https://api.ipify.org"  # IPv4 only
        response = urllib.request.urlopen(url, timeout=5)
        external_ip = response.read().decode('utf-8')
        logging.info(f"External IP: {external_ip}")
        return external_ip
    except Exception as e:
        logging.error(f"Error: {e.reason.strerror}")
        return


def fetch_dns_records(cf: Cloudflare, zone_id: str, type: str = 'ALL') -> list:
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
        'ALL': lambda record: True,
        'A': lambda record: isinstance(record, ARecord),
        'CNAME': lambda record: isinstance(record, CNAMERecord),
    }
    filter_func = record_types.get(type, None)
    if filter_func is None:
        raise ValueError(f"Invalid record type: {type}")

    records = cf.dns.records.list(zone_id=zone_id)
    logging.info(f"DNS records fetched: {len(list(records))}")

    logging.info(f"Filtering records by type: {type}")
    filtered_records = [record for record in records if filter_func(record)]
    logging.info(f"DNS records filtered: {len(filtered_records)}")

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
            logging.info(f"No changes needed for {record.name}")
            continue
        try:
            cf.dns.records.update(
                dns_record_id=record.id,
                zone_id=zone_id,
                content=ip,
                name=record.name,
                type=record.type,
                ttl=record.ttl,
                comment=f"Updated by rpi-cloudflare-ddns on {datetime.now()}"
            )
        except Exception as e:
            logging.error(f"Error: {e.reason.strerror}")


def main():
    external_ip = get_public_ip()
    if external_ip is None:
        return
    cf = Cloudflare()
    dns_records = fetch_dns_records(cf, os.getenv('ZONE_ID'), type='A')
    update_dns_records(cf, dns_records, os.getenv('ZONE_ID'), ip=external_ip)
    logging.info("DNS records updated successfully")


if __name__ == '__main__':
    main()
