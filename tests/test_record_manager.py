"""
Tests for the DNS Record Management system
"""

from dataclasses import dataclass
from unittest.mock import Mock, patch

import pytest

from record_manager import (
    BatchRecordManager,
    CloudflareClientAdapter,
    DnsUpdateRequest,
    RecordManager,
    RecordManagerError,
    UpdateResult,
    ZoneUpdateSummary,
    create_batch_record_manager,
    create_record_manager,
)

TEST_ZONE_NAME = "example.com"
TEST_ZONE_ID = "zone-123"


# Test fixtures and mock objects
@dataclass
class MockRecord:
    """Mock DNS record for testing"""

    id: str
    name: str
    content: str
    type: str = "A"
    proxied: bool = False
    ttl: int = 300

    def __eq__(self, other):
        if not isinstance(other, MockRecord):
            return False
        return (
            self.id == other.id
            and self.name == other.name
            and self.content == other.content
            and self.type == other.type
        )


@pytest.fixture
def mock_zone_config():
    """Mock zone configuration for testing"""
    from configuration_manager import CloudflareZoneConfig, SubdomainConfig

    mock_client = Mock()

    config = CloudflareZoneConfig(
        zone_id="test-zone-123",
        subdomains=[
            SubdomainConfig(name="www", proxied=False, ttl=300),
            SubdomainConfig(name="api", proxied=True, ttl=600),
            SubdomainConfig(name="@", proxied=False, ttl=300),  # Root domain
        ],
        authentication=Mock(),
    )

    # Set runtime fields
    config.zone_name = TEST_ZONE_NAME
    config.client = mock_client

    return config


@pytest.fixture
def sample_records():
    """Sample DNS records for testing"""
    return [
        MockRecord(id="rec1", name="www.example.com", content="192.168.1.1"),
        MockRecord(
            id="rec2",
            name="api.example.com",
            content="192.168.1.1",
            proxied=True,
            ttl=600,
        ),
        MockRecord(id="rec3", name=TEST_ZONE_NAME, content="192.168.1.1"),
        MockRecord(
            id="rec4", name="mail.example.com", content="192.168.1.2"
        ),  # Not in config
    ]


class TestCloudflareClientAdapter:
    """Test the Cloudflare client adapter"""

    def test_list_records_success(self):
        """Test successful record listing"""
        mock_client = Mock()
        mock_records = [
            MockRecord(id="rec1", name="www.example.com", content="192.168.1.1"),
            MockRecord(id="rec2", name="api.example.com", content="192.168.1.2"),
        ]
        mock_client.dns.records.list.return_value = mock_records

        adapter = CloudflareClientAdapter(mock_client)

        # Patch isinstance to treat MockRecord as A
        with patch("record_manager.isinstance") as mock_isinstance:
            mock_isinstance.side_effect = lambda obj, cls: isinstance(obj, MockRecord)

            records = adapter.list_records(TEST_ZONE_ID)

            assert records == mock_records
            mock_client.dns.records.list.assert_called_once_with(zone_id=TEST_ZONE_ID)

    def test_list_records_filters_a_records(self):
        """Test that only A records are returned"""
        mock_client = Mock()

        a_record = MockRecord(id="rec1", name="www.example.com", content="192.168.1.1")
        # Simulate A record by making it an instance of A (simplified)
        mock_client.dns.records.list.return_value = [a_record, "not-an-a-record"]

        adapter = CloudflareClientAdapter(mock_client)

        # Mock isinstance to return True only for our a_record
        with patch("record_manager.isinstance") as mock_isinstance:
            mock_isinstance.side_effect = lambda obj, cls: obj == a_record

            records = adapter.list_records(TEST_ZONE_ID)

            assert len(records) == 1
            assert records[0] == a_record

    def test_list_records_error(self):
        """Test error handling in record listing"""
        mock_client = Mock()
        mock_client.dns.records.list.side_effect = Exception("API Error")

        adapter = CloudflareClientAdapter(mock_client)

        with pytest.raises(RecordManagerError) as exc_info:
            adapter.list_records(TEST_ZONE_ID)

        assert "Failed to fetch records for zone zone-123" in str(exc_info.value)
        assert "API Error" in str(exc_info.value)

    def test_update_record_success(self):
        """Test successful record update"""
        mock_client = Mock()
        mock_client.dns.records.update.return_value = Mock()

        adapter = CloudflareClientAdapter(mock_client)

        result = adapter.update_record(
            zone_id=TEST_ZONE_ID,
            record_id="rec-456",
            content="192.168.1.100",
            name="www.example.com",
            type="A",
        )

        assert result is True
        mock_client.dns.records.update.assert_called_once_with(
            dns_record_id="rec-456",
            zone_id=TEST_ZONE_ID,
            content="192.168.1.100",
            name="www.example.com",
            type="A",
        )

    def test_update_record_error(self):
        """Test error handling in record update"""
        mock_client = Mock()
        mock_client.dns.records.update.side_effect = Exception("Update failed")

        adapter = CloudflareClientAdapter(mock_client)

        result = adapter.update_record(
            zone_id=TEST_ZONE_ID, record_id="rec-456", content="192.168.1.100"
        )

        assert result is False


class TestRecordManager:
    """Test the main RecordManager class"""

    def test_init_with_client(self):
        """Test initialization with client"""
        mock_client = Mock()
        manager = RecordManager(mock_client)

        assert manager.client == mock_client

    def test_init_without_client(self):
        """Test initialization without client"""
        manager = RecordManager()

        assert manager.client is None

    def test_build_fqdn_subdomain(self):
        """Test FQDN building for subdomains"""
        manager = RecordManager()

        fqdn = manager.build_fqdn("www", TEST_ZONE_NAME)

        assert fqdn == "www.example.com"

    def test_build_fqdn_root_domain_at_symbol(self):
        """Test FQDN building for root domain with @ symbol"""
        manager = RecordManager()

        fqdn = manager.build_fqdn("@", TEST_ZONE_NAME)

        assert fqdn == TEST_ZONE_NAME

    def test_build_fqdn_root_domain_empty(self):
        """Test FQDN building for root domain with empty string"""
        manager = RecordManager()

        fqdn = manager.build_fqdn("", TEST_ZONE_NAME)

        assert fqdn == TEST_ZONE_NAME

    def test_build_fqdn_case_insensitive(self):
        """Test FQDN building is case insensitive"""
        manager = RecordManager()

        fqdn = manager.build_fqdn("WWW", TEST_ZONE_NAME)

        assert fqdn == "www.example.com"

    def test_build_fqdn_strips_whitespace(self):
        """Test FQDN building strips whitespace"""
        manager = RecordManager()

        fqdn = manager.build_fqdn("  www  ", TEST_ZONE_NAME)

        assert fqdn == "www.example.com"

    def test_fetch_zone_records_success(self, mock_zone_config, sample_records):
        """Test successful zone record fetching"""
        manager = RecordManager()

        # Mock the adapter
        with patch("record_manager.CloudflareClientAdapter") as MockAdapter:
            mock_adapter = Mock()
            mock_adapter.list_records.return_value = sample_records
            MockAdapter.return_value = mock_adapter

            records = manager.fetch_zone_records(mock_zone_config)

            assert records == sample_records
            MockAdapter.assert_called_once_with(mock_zone_config.client)
            mock_adapter.list_records.assert_called_once_with(mock_zone_config.zone_id)

    def test_fetch_zone_records_no_client(self, mock_zone_config):
        """Test fetch_zone_records with no client configured"""
        mock_zone_config.client = None
        manager = RecordManager()

        with pytest.raises(RecordManagerError) as exc_info:
            manager.fetch_zone_records(mock_zone_config)

        assert "No client configured for zone" in str(exc_info.value)

    def test_prepare_updates_identifies_changes(self, mock_zone_config, sample_records):
        """Test prepare_updates identifies records that need updating"""
        manager = RecordManager()
        new_ip = "10.0.0.1"

        updates = manager.prepare_updates(mock_zone_config, sample_records, new_ip)

        # Should identify 3 records that need updating (www, api, root)
        assert len(updates) == 3

        # Check each update
        update_by_fqdn = {u.fqdn: u for u in updates}

        www_update = update_by_fqdn["www.example.com"]
        assert www_update.current_content == "192.168.1.1"
        assert www_update.new_content == new_ip
        assert www_update.proxied is False
        assert www_update.ttl == 300

        api_update = update_by_fqdn["api.example.com"]
        assert api_update.current_content == "192.168.1.1"
        assert api_update.new_content == new_ip
        assert api_update.proxied is True
        assert api_update.ttl == 600

        root_update = update_by_fqdn[TEST_ZONE_NAME]
        assert root_update.current_content == "192.168.1.1"
        assert root_update.new_content == new_ip

    def test_prepare_updates_no_changes_needed(self, mock_zone_config, sample_records):
        """Test prepare_updates when no changes are needed"""
        manager = RecordManager()
        current_ip = "192.168.1.1"  # Same as records

        updates = manager.prepare_updates(mock_zone_config, sample_records, current_ip)

        # Should find no updates needed (records already have correct IP)
        assert len(updates) == 0

    def test_prepare_updates_missing_records(self, mock_zone_config):
        """Test prepare_updates with missing DNS records"""
        manager = RecordManager()
        empty_records = []
        new_ip = "10.0.0.1"

        with patch("record_manager.logger") as mock_logger:
            updates = manager.prepare_updates(mock_zone_config, empty_records, new_ip)

            assert len(updates) == 0
            # Should log warnings about missing records
            assert mock_logger.warning.call_count == 3  # 3 subdomains configured

    def test_prepare_updates_no_zone_name(self, mock_zone_config):
        """Test prepare_updates with no zone name"""
        mock_zone_config.zone_name = None
        manager = RecordManager()

        with pytest.raises(RecordManagerError) as exc_info:
            manager.prepare_updates(mock_zone_config, [], "10.0.0.1")

        assert "Zone name not populated" in str(exc_info.value)

    def test_execute_updates_success(self, mock_zone_config):
        """Test successful execution of updates"""
        manager = RecordManager()

        updates = [
            DnsUpdateRequest(
                zone_id="test-zone-123",
                fqdn="www.example.com",
                record_id="rec1",
                record_type="A",
                proxied=False,
                current_content="192.168.1.1",
                new_content="10.0.0.1",
                ttl=300,
            )
        ]

        with patch("record_manager.CloudflareClientAdapter") as MockAdapter:
            mock_adapter = Mock()
            mock_adapter.update_record.return_value = True
            MockAdapter.return_value = mock_adapter

            summary = manager.execute_updates(mock_zone_config, updates)

            assert summary.total_records == 1
            assert summary.successful_updates == 1
            assert summary.failed_updates == 0
            assert len(summary.results) == 1

            result = summary.results[0]
            assert result.success is True
            assert result.fqdn == "www.example.com"
            assert result.old_ip == "192.168.1.1"
            assert result.new_ip == "10.0.0.1"

    def test_execute_updates_partial_failure(self, mock_zone_config):
        """Test execution with some updates failing"""
        manager = RecordManager()

        updates = [
            DnsUpdateRequest(
                zone_id="test-zone-123",
                fqdn="www.example.com",
                record_id="rec1",
                record_type="A",
                proxied=False,
                current_content="192.168.1.1",
                new_content="10.0.0.1",
                ttl=300,
            ),
            DnsUpdateRequest(
                zone_id="test-zone-123",
                fqdn="api.example.com",
                record_id="rec2",
                record_type="A",
                proxied=True,
                current_content="192.168.1.1",
                new_content="10.0.0.1",
                ttl=600,
            ),
        ]

        with patch("record_manager.CloudflareClientAdapter") as MockAdapter:
            mock_adapter = Mock()
            # First update succeeds, second fails
            mock_adapter.update_record.side_effect = [True, False]
            MockAdapter.return_value = mock_adapter

            summary = manager.execute_updates(mock_zone_config, updates)

            assert summary.total_records == 2
            assert summary.successful_updates == 1
            assert summary.failed_updates == 1

            # Check individual results
            successful = [r for r in summary.results if r.success]
            failed = [r for r in summary.results if not r.success]

            assert len(successful) == 1
            assert len(failed) == 1
            assert successful[0].fqdn == "www.example.com"
            assert failed[0].fqdn == "api.example.com"

    def test_execute_updates_exception_handling(self, mock_zone_config):
        """Test exception handling during updates"""
        manager = RecordManager()

        updates = [
            DnsUpdateRequest(
                zone_id="test-zone-123",
                fqdn="www.example.com",
                record_id="rec1",
                record_type="A",
                proxied=False,
                current_content="192.168.1.1",
                new_content="10.0.0.1",
                ttl=300,
            )
        ]

        with patch("record_manager.CloudflareClientAdapter") as MockAdapter:
            mock_adapter = Mock()
            mock_adapter.update_record.side_effect = Exception("Network error")
            MockAdapter.return_value = mock_adapter

            summary = manager.execute_updates(mock_zone_config, updates)

            assert summary.total_records == 1
            assert summary.successful_updates == 0
            assert summary.failed_updates == 1

            result = summary.results[0]
            assert result.success is False
            assert "Network error" in result.error_message

    def test_execute_updates_no_client(self, mock_zone_config):
        """Test execute_updates with no client configured"""
        mock_zone_config.client = None
        manager = RecordManager()

        with pytest.raises(RecordManagerError) as exc_info:
            manager.execute_updates(mock_zone_config, [])

        assert "No client configured for zone" in str(exc_info.value)

    def test_update_zone_records_complete_workflow(
        self, mock_zone_config, sample_records
    ):
        """Test complete workflow: fetch, prepare, execute"""
        manager = RecordManager()
        new_ip = "10.0.0.1"

        with (
            patch.object(manager, "fetch_zone_records") as mock_fetch,
            patch.object(manager, "prepare_updates") as mock_prepare,
            patch.object(manager, "execute_updates") as mock_execute,
        ):
            mock_fetch.return_value = sample_records
            mock_updates = [Mock()]
            mock_prepare.return_value = mock_updates
            mock_summary = Mock()
            mock_execute.return_value = mock_summary

            result = manager.update_zone_records(mock_zone_config, new_ip)

            assert result == mock_summary
            mock_fetch.assert_called_once_with(mock_zone_config)
            mock_prepare.assert_called_once_with(
                mock_zone_config, sample_records, new_ip
            )
            mock_execute.assert_called_once_with(mock_zone_config, mock_updates)

    def test_update_zone_records_no_updates_needed(self, mock_zone_config):
        """Test workflow when no updates are needed"""
        manager = RecordManager()
        new_ip = "10.0.0.1"

        with (
            patch.object(manager, "fetch_zone_records") as mock_fetch,
            patch.object(manager, "prepare_updates") as mock_prepare,
        ):
            mock_fetch.return_value = []
            mock_prepare.return_value = []  # No updates needed

            result = manager.update_zone_records(mock_zone_config, new_ip)

            assert result.total_records == 0
            assert result.successful_updates == 0
            assert result.failed_updates == 0
            assert len(result.results) == 0


class TestBatchRecordManager:
    """Test the BatchRecordManager class"""

    def test_init_with_record_manager(self):
        """Test initialization with provided record manager"""
        mock_manager = Mock()
        batch_manager = BatchRecordManager(mock_manager)

        assert batch_manager.record_manager == mock_manager

    def test_init_without_record_manager(self):
        """Test initialization creates default record manager"""
        batch_manager = BatchRecordManager()

        assert isinstance(batch_manager.record_manager, RecordManager)

    def test_update_all_zones_success(self):
        """Test successful update of all zones"""
        mock_manager = Mock()
        batch_manager = BatchRecordManager(mock_manager)

        # Mock zone configs
        zone1 = Mock()
        zone1.zone_name = TEST_ZONE_NAME
        zone1.zone_id = "zone1"

        zone2 = Mock()
        zone2.zone_name = "test.com"
        zone2.zone_id = "zone2"

        zones = [zone1, zone2]

        # Mock summaries
        summary1 = Mock()
        summary1.total_records = 2
        summary1.successful_updates = 2

        summary2 = Mock()
        summary2.total_records = 1
        summary2.successful_updates = 1

        mock_manager.update_zone_records.side_effect = [summary1, summary2]

        summaries = batch_manager.update_all_zones(zones, "10.0.0.1")

        assert len(summaries) == 2
        assert summaries[0] == summary1
        assert summaries[1] == summary2

        # Verify each zone was processed
        assert mock_manager.update_zone_records.call_count == 2
        mock_manager.update_zone_records.assert_any_call(zone1, "10.0.0.1")
        mock_manager.update_zone_records.assert_any_call(zone2, "10.0.0.1")

    def test_update_all_zones_with_failures(self):
        """Test update_all_zones handles individual zone failures"""
        mock_manager = Mock()
        batch_manager = BatchRecordManager(mock_manager)

        zone1 = Mock()
        zone1.zone_name = TEST_ZONE_NAME
        zone1.zone_id = "zone1"

        zone2 = Mock()
        zone2.zone_name = "test.com"
        zone2.zone_id = "zone2"

        zones = [zone1, zone2]

        # First zone succeeds, second fails
        summary1 = Mock()
        summary1.total_records = 1
        summary1.successful_updates = 1
        summary1.failed_updates = 0

        mock_manager.update_zone_records.side_effect = [
            summary1,
            Exception("Zone 2 failed"),
        ]

        summaries = batch_manager.update_all_zones(zones, "10.0.0.1")  # type: ignore

        assert len(summaries) == 2
        assert summaries[0] == summary1

        # Second summary should be a failure summary
        failed_summary = summaries[1]
        assert failed_summary.zone_name == "test.com"
        assert failed_summary.total_records == 0
        assert failed_summary.successful_updates == 0
        assert failed_summary.failed_updates == 1

    def test_get_overall_summary(self):
        """Test overall summary calculation"""
        batch_manager = BatchRecordManager()

        # Create mock summaries
        summary1 = Mock()
        summary1.total_records = 3
        summary1.successful_updates = 2
        summary1.failed_updates = 1

        summary2 = Mock()
        summary2.total_records = 2
        summary2.successful_updates = 2
        summary2.failed_updates = 0

        summary3 = Mock()  # Failed zone
        summary3.total_records = 0
        summary3.successful_updates = 0
        summary3.failed_updates = 1

        summaries = [summary1, summary2, summary3]

        overall = batch_manager.get_overall_summary(summaries)

        assert overall["total_zones"] == 3
        assert (
            overall["successful_zones"] == 1
        )  # Only summary2 had no failures and >0 records
        assert overall["total_records"] == 5  # 3 + 2 + 0
        assert overall["successful_updates"] == 4  # 2 + 2 + 0
        assert overall["failed_updates"] == 2  # 1 + 0 + 1


class TestZoneUpdateSummary:
    """Test the ZoneUpdateSummary dataclass"""

    def test_success_rate_calculation(self):
        """Test success rate calculation"""
        summary = ZoneUpdateSummary(
            zone_name=TEST_ZONE_NAME,
            zone_id=TEST_ZONE_ID,
            total_records=10,
            successful_updates=8,
            failed_updates=2,
            results=[],
        )

        assert summary.success_rate == 80.0

    def test_success_rate_zero_records(self):
        """Test success rate with zero records"""
        summary = ZoneUpdateSummary(
            zone_name=TEST_ZONE_NAME,
            zone_id=TEST_ZONE_ID,
            total_records=0,
            successful_updates=0,
            failed_updates=0,
            results=[],
        )

        assert summary.success_rate == 100.0

    def test_str_representation(self):
        """Test string representation"""
        summary = ZoneUpdateSummary(
            zone_name=TEST_ZONE_NAME,
            zone_id=TEST_ZONE_ID,
            total_records=5,
            successful_updates=4,
            failed_updates=1,
            results=[],
        )

        str_repr = str(summary)
        assert TEST_ZONE_NAME in str_repr
        assert "4/5" in str_repr
        assert "80.0%" in str_repr


class TestUpdateResult:
    """Test the UpdateResult dataclass"""

    def test_successful_result_str(self):
        """Test string representation of successful result"""
        result = UpdateResult(
            fqdn="www.example.com",
            success=True,
            old_ip="192.168.1.1",
            new_ip="10.0.0.1",
        )

        str_repr = str(result)
        assert "www.example.com" in str_repr
        assert "192.168.1.1 → 10.0.0.1" in str_repr

    def test_failed_result_str(self):
        """Test string representation of failed result"""
        result = UpdateResult(
            fqdn="www.example.com",
            success=False,
            old_ip="192.168.1.1",
            new_ip="10.0.0.1",
            error_message="Update failed",
        )

        str_repr = str(result)
        assert "www.example.com" in str_repr
        assert "Failed - Update failed" in str_repr


class TestDnsUpdateRequest:
    """Test the DnsUpdateRequest dataclass"""

    def test_str_representation(self):
        """Test string representation"""
        request = DnsUpdateRequest(
            zone_id=TEST_ZONE_ID,
            fqdn="www.example.com",
            record_id="rec-456",
            record_type="A",
            proxied=False,
            current_content="192.168.1.1",
            new_content="10.0.0.1",
            ttl=300,
        )

        str_repr = str(request)
        assert "www.example.com" in str_repr
        assert "192.168.1.1 → 10.0.0.1" in str_repr
        assert "TTL=300" in str_repr


class TestFactoryFunctions:
    """Test factory functions"""

    def test_create_record_manager(self):
        """Test record manager factory"""
        manager = create_record_manager()

        assert isinstance(manager, RecordManager)
        assert manager.client is None

    def test_create_batch_record_manager(self):
        """Test batch record manager factory"""
        batch_manager = create_batch_record_manager()

        assert isinstance(batch_manager, BatchRecordManager)
        assert isinstance(batch_manager.record_manager, RecordManager)


class TestRecordManagerIntegration:
    """Integration tests simulating real usage patterns"""

    def test_end_to_end_update_workflow(self, mock_zone_config):
        """Test complete end-to-end update workflow"""
        manager = RecordManager()

        # Mock records that need updating
        mock_records = [
            MockRecord(id="rec1", name="www.example.com", content="192.168.1.1"),
            MockRecord(id="rec2", name=TEST_ZONE_NAME, content="192.168.1.1"),
        ]

        new_ip = "10.0.0.1"

        with patch("record_manager.CloudflareClientAdapter") as MockAdapter:
            mock_adapter = Mock()
            mock_adapter.list_records.return_value = mock_records
            mock_adapter.update_record.return_value = True
            MockAdapter.return_value = mock_adapter

            summary = manager.update_zone_records(mock_zone_config, new_ip)

            # Should have updated 2 records successfully
            assert summary.total_records == 2
            assert summary.successful_updates == 2
            assert summary.failed_updates == 0
            assert summary.success_rate == 100.0

            # Verify adapter methods were called correctly
            mock_adapter.list_records.assert_called_once()
            assert mock_adapter.update_record.call_count == 2

    def test_batch_processing_multiple_zones(self):
        """Test batch processing of multiple zones"""
        batch_manager = BatchRecordManager()

        # Create multiple zone configs
        zones = []
        for i in range(3):
            zone = Mock()
            zone.zone_name = f"example{i}.com"
            zone.zone_id = f"zone-{i}"
            zones.append(zone)

        # Mock the record manager to return successful summaries
        with patch.object(
            batch_manager.record_manager, "update_zone_records"
        ) as mock_update:
            mock_summaries = []
            for i in range(3):
                summary = Mock()
                summary.total_records = 2
                summary.successful_updates = 2
                summary.failed_updates = 0
                mock_summaries.append(summary)

            mock_update.side_effect = mock_summaries

            summaries = batch_manager.update_all_zones(zones, "10.0.0.1")

            assert len(summaries) == 3
            assert mock_update.call_count == 3

            # Check overall statistics
            overall = batch_manager.get_overall_summary(summaries)
            assert overall["total_zones"] == 3
            assert overall["successful_zones"] == 3
            assert overall["total_records"] == 6
            assert overall["successful_updates"] == 6
            assert overall["failed_updates"] == 0


# Error condition tests
class TestErrorConditions:
    """Test various error conditions and edge cases"""

    def test_record_manager_error_inheritance(self):
        """Test RecordManagerError is properly defined"""
        error = RecordManagerError("Test error")

        assert isinstance(error, Exception)
        assert str(error) == "Test error"

    def test_cloudflare_adapter_logs_debug_info(self):
        """Test that adapter logs debug information"""
        mock_client = Mock()
        mock_records = [MockRecord(id="rec1", name="test.com", content="1.1.1.1")]
        mock_client.dns.records.list.return_value = mock_records

        adapter = CloudflareClientAdapter(mock_client)

        with patch("record_manager.logger") as mock_logger:
            adapter.list_records(TEST_ZONE_ID)

            # Should log debug info about number of records
            mock_logger.debug.assert_called()
            debug_call = mock_logger.debug.call_args[0][0]
            assert "Retrieved" in debug_call and "A records" in debug_call

    def test_missing_zone_name_error(self):
        """Test error when zone name is missing"""
        manager = RecordManager()

        # Create config without zone_name
        config = Mock()
        config.zone_name = None
        config.zone_id = TEST_ZONE_ID

        with pytest.raises(RecordManagerError) as exc_info:
            manager.prepare_updates(config, [], "10.0.0.1")
            assert "zone_name" in str(exc_info.value).lower()
