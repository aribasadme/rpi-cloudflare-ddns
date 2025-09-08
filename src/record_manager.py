"""
DNS Record Management Module - handles DNS record operations and updates
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Protocol

from cloudflare import Cloudflare
from cloudflare.types.dns.record_response import A
from typing_extensions import Literal

from configuration_manager import CloudflareZoneConfig

logger = logging.getLogger(__name__)


class RecordManagerError(Exception):
    """Custom exception for record management errors"""

    pass


@dataclass
class DnsUpdateRequest:
    """Represents a DNS record update request"""

    zone_id: str
    fqdn: str
    record_id: str
    record_type: Optional[Literal["A"]]
    proxied: bool
    current_content: str
    new_content: str
    ttl: int

    def __str__(self) -> str:
        return (
            f"{self.fqdn}: {self.current_content} → {self.new_content} (TTL={self.ttl})"
        )


@dataclass
class UpdateResult:
    """Result of a DNS record update operation"""

    fqdn: str
    success: bool
    old_ip: str
    new_ip: str
    error_message: Optional[str] = None

    def __str__(self) -> str:
        if self.success:
            return f"✓ {self.fqdn}: {self.old_ip} → {self.new_ip}"
        else:
            return f"✗ {self.fqdn}: Failed - {self.error_message}"


@dataclass
class ZoneUpdateSummary:
    """Summary of updates for a zone"""

    zone_name: str
    zone_id: str
    total_records: int
    successful_updates: int
    failed_updates: int
    results: List[UpdateResult]

    @property
    def success_rate(self) -> float:
        """Calculate success rate as percentage"""
        if self.total_records == 0:
            return 100.0
        return (self.successful_updates / self.total_records) * 100

    def __str__(self) -> str:
        return (
            f"{self.zone_name}: {self.successful_updates}/{self.total_records} "
            f"successful ({self.success_rate:.1f}%)"
        )


class CloudflareClientProtocol(Protocol):
    """Protocol for Cloudflare client interface"""

    def list_records(self, zone_id: str) -> List[A]:
        """List DNS records for a zone"""
        ...

    def update_record(self, zone_id: str, record_id: str, **kwargs) -> bool:
        """Update a DNS record"""
        ...


class CloudflareClientAdapter:
    """Adapter for the Cloudflare client to match our protocol"""

    def __init__(self, client: Cloudflare):
        self.client = client

    def list_records(self, zone_id: str) -> List[A]:
        """List A records for the specified zone"""
        try:
            records = self.client.dns.records.list(zone_id=zone_id)
            a_records = [record for record in records if isinstance(record, A)]
            logger.debug(f"Retrieved {len(a_records)} A records for zone {zone_id}")
            return a_records
        except Exception as e:
            logger.error(f"Error fetching records for zone {zone_id}: {e}")
            raise RecordManagerError(f"Failed to fetch records for zone {zone_id}: {e}")

    def update_record(self, zone_id: str, record_id: str, **kwargs) -> bool:
        """Update a DNS record"""
        try:
            self.client.dns.records.update(
                dns_record_id=record_id, zone_id=zone_id, **kwargs
            )
            return True
        except Exception as e:
            logger.error(f"Error updating record {record_id}: {e}")
            return False


class RecordManager:
    """Manages DNS record operations for Cloudflare zones"""

    def __init__(self, client: Optional[CloudflareClientProtocol] = None):
        """Initialize the record manager

        Args:
            client: Cloudflare client adapter. If None, will be set per operation.
        """
        self.client = client

    def fetch_zone_records(self, zone_config: CloudflareZoneConfig) -> List[A]:
        """Fetch all A records for a zone

        Args:
            zone_config: Zone configuration with client attached

        Returns:
            List of A records

        Raises:
            RecordManagerError: If records cannot be fetched
        """
        if not zone_config.client:
            raise RecordManagerError(
                f"No client configured for zone {zone_config.zone_id}"
            )

        client = CloudflareClientAdapter(zone_config.client)
        return client.list_records(zone_config.zone_id)

    def build_fqdn(self, subdomain_name: str, base_domain: str) -> str:
        """Build fully qualified domain name from subdomain and base domain

        Args:
            subdomain_name: Subdomain name (can be '@' for root)
            base_domain: Base domain name

        Returns:
            Fully qualified domain name
        """
        name = subdomain_name.lower().strip()

        # Handle root domain
        if name == "" or name == "@":
            return base_domain

        return f"{name}.{base_domain}"

    def prepare_updates(
        self, zone_config: CloudflareZoneConfig, records: List[A], new_ip: str
    ) -> List[DnsUpdateRequest]:
        """Identify DNS records that need IP address updates

        Args:
            zone_config: Zone configuration
            records: Current DNS records
            new_ip: New IP address to set

        Returns:
            List of update requests for records that need updating
        """
        if not zone_config.zone_name:
            raise RecordManagerError(
                f"Zone name not populated for zone {zone_config.zone_id}"
            )

        updates: List[DnsUpdateRequest] = []
        base_domain = str(zone_config.zone_name)

        # Create lookup map for existing A records
        record_map: Dict[str, A] = {}
        for record in records:
            if record.name is not None:
                record_map[record.name.lower()] = record

        # Check each configured subdomain
        for subdomain_config in zone_config.subdomains:
            fqdn = self.build_fqdn(subdomain_config.name, base_domain)

            # Check if record exists
            if record := record_map.get(fqdn):
                if record.content != new_ip:
                    effective_ttl = zone_config.get_effective_ttl(subdomain_config)

                    update_request = DnsUpdateRequest(
                        zone_id=zone_config.zone_id,
                        fqdn=fqdn,
                        record_id=record.id,
                        record_type=record.type,
                        proxied=subdomain_config.proxied,
                        current_content=str(record.content),
                        new_content=new_ip,
                        ttl=effective_ttl,
                    )

                    updates.append(update_request)
                    logger.debug(f"Queued update: {update_request}")
                else:
                    logger.debug(f"Record {fqdn} already has correct IP: {new_ip}")
            else:
                logger.warning(f"DNS record not found: {fqdn}")

        return updates

    def execute_updates(
        self, zone_config: CloudflareZoneConfig, updates: List[DnsUpdateRequest]
    ) -> ZoneUpdateSummary:
        """Execute DNS record updates for a zone

        Args:
            zone_config: Zone configuration with client attached
            updates: List of update requests

        Returns:
            Summary of update results
        """
        if not zone_config.client:
            raise RecordManagerError(
                f"No client configured for zone {zone_config.zone_id}"
            )

        client = CloudflareClientAdapter(zone_config.client)
        results: List[UpdateResult] = []

        for update in updates:
            try:
                success = client.update_record(
                    zone_id=update.zone_id,
                    record_id=update.record_id,
                    content=update.new_content,
                    name=update.fqdn,
                    type=update.record_type,
                    proxied=update.proxied,
                    ttl=update.ttl,
                    comment=f"Updated by rpi-cloudflare-ddns on {datetime.now()}",
                )

                result = UpdateResult(
                    fqdn=update.fqdn,
                    success=success,
                    old_ip=update.current_content,
                    new_ip=update.new_content,
                    error_message=None if success else "Update operation failed",
                )

                if success:
                    logger.info(
                        f"Updated {update.fqdn} from {update.current_content} to {update.new_content}"
                    )
                    logger.debug(
                        f"Updated {update.fqdn} from {update.current_content} to {update.new_content} "
                        f"(type: {update.record_type}, proxied: {update.proxied}, ttl: {update.ttl})"
                    )
                else:
                    logger.error(f"Failed to update {update.fqdn}")

                results.append(result)

            except Exception as e:
                error_msg = f"Exception during update: {e}"
                logger.error(f"Failed to update {update.fqdn}: {error_msg}")

                result = UpdateResult(
                    fqdn=update.fqdn,
                    success=False,
                    old_ip=update.current_content,
                    new_ip=update.new_content,
                    error_message=error_msg,
                )
                results.append(result)

        # Calculate summary
        successful_updates = sum(1 for r in results if r.success)
        failed_updates = len(results) - successful_updates

        summary = ZoneUpdateSummary(
            zone_name=zone_config.zone_name or zone_config.zone_id,
            zone_id=zone_config.zone_id,
            total_records=len(results),
            successful_updates=successful_updates,
            failed_updates=failed_updates,
            results=results,
        )

        if failed_updates > 0:
            logger.warning(f"Zone {summary.zone_name}: {failed_updates} updates failed")

        return summary

    def update_zone_records(
        self, zone_config: CloudflareZoneConfig, new_ip: str
    ) -> ZoneUpdateSummary:
        """Complete workflow: fetch records, prepare updates, and execute them

        Args:
            zone_config: Zone configuration with client attached
            new_ip: New IP address to set

        Returns:
            Summary of update results
        """
        try:
            # Fetch current records
            records = self.fetch_zone_records(zone_config)

            # Prepare updates
            updates = self.prepare_updates(zone_config, records, new_ip)

            if not updates:
                logger.debug(f"No records need updating for {zone_config.zone_name}")
                return ZoneUpdateSummary(
                    zone_name=zone_config.zone_name or zone_config.zone_id,
                    zone_id=zone_config.zone_id,
                    total_records=0,
                    successful_updates=0,
                    failed_updates=0,
                    results=[],
                )

            # Execute updates
            summary = self.execute_updates(zone_config, updates)

            logger.info(f"Zone update summary: {summary}")
            return summary

        except RecordManagerError:
            raise
        except Exception as e:
            raise RecordManagerError(
                f"Failed to update zone {zone_config.zone_id}: {e}"
            )


class BatchRecordManager:
    """Manages DNS record operations across multiple zones"""

    def __init__(self, record_manager: Optional[RecordManager] = None):
        """Initialize the batch record manager

        Args:
            record_manager: RecordManager instance. If None, creates a new one.
        """
        self.record_manager = record_manager or RecordManager()

    def update_all_zones(
        self, zone_configs: List[CloudflareZoneConfig], new_ip: str
    ) -> List[ZoneUpdateSummary]:
        """Update DNS records across multiple zones

        Args:
            zone_configs: List of zone configurations with clients attached
            new_ip: New IP address to set

        Returns:
            List of update summaries for each zone
        """
        summaries: List[ZoneUpdateSummary] = []

        for zone_config in zone_configs:
            try:
                logger.debug(f"Processing zone: {zone_config.zone_name}")
                summary = self.record_manager.update_zone_records(zone_config, new_ip)
                summaries.append(summary)

                if summary.total_records > 0:
                    logger.info(
                        f"Updated {summary.successful_updates} DNS records for {summary.zone_name}"
                    )

            except Exception as e:
                logger.error(
                    f"Error processing zone {zone_config.zone_name or zone_config.zone_id}: {e}"
                )

                # Create a failed summary
                failed_summary = ZoneUpdateSummary(
                    zone_name=zone_config.zone_name or zone_config.zone_id,
                    zone_id=zone_config.zone_id,
                    total_records=0,
                    successful_updates=0,
                    failed_updates=1,
                    results=[],
                )
                summaries.append(failed_summary)
                continue

        return summaries

    def get_overall_summary(self, summaries: List[ZoneUpdateSummary]) -> Dict[str, int]:
        """Get overall summary statistics

        Args:
            summaries: List of zone update summaries

        Returns:
            Dictionary with overall statistics
        """
        total_zones = len(summaries)
        total_records = sum(s.total_records for s in summaries)
        total_successful = sum(s.successful_updates for s in summaries)
        total_failed = sum(s.failed_updates for s in summaries)
        successful_zones = sum(
            1 for s in summaries if s.failed_updates == 0 and s.total_records > 0
        )

        return {
            "total_zones": total_zones,
            "successful_zones": successful_zones,
            "total_records": total_records,
            "successful_updates": total_successful,
            "failed_updates": total_failed,
        }


# Factory functions
def create_record_manager() -> RecordManager:
    """Create a RecordManager with default settings"""
    return RecordManager()


def create_batch_record_manager() -> BatchRecordManager:
    """Create a BatchRecordManager with default settings"""
    return BatchRecordManager()


# Example usage
if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    print("🔧 DNS Record Manager Examples:")
    print()

    # Example 1: Basic record manager usage
    print("📋 Record Manager:")
    print("   - Fetches current DNS records")
    print("   - Identifies records that need updating")
    print("   - Executes updates with proper error handling")
    print("   - Provides detailed update summaries")

    print()
    print("🔄 Batch Record Manager:")
    print("   - Handles multiple zones in a single operation")
    print("   - Provides overall statistics")
    print("   - Continues processing even if individual zones fail")

    print()
    print("✨ Key Features:")
    print("   - Protocol-based design for easy testing")
    print("   - Comprehensive error handling and logging")
    print("   - Detailed update results and summaries")
    print("   - Clean separation of concerns")
    print("   - Type-safe operations with dataclasses")
