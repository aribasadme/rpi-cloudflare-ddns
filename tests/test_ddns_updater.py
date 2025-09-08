from unittest.mock import Mock, patch

import pytest

from configuration_manager import ConfigurationError
from ddns_updater import (
    AuthenticationError,
    DNSUpdater,
    DNSUpdaterError,
    UpdateCycleResult,
)
from ip_provider import IPProviderError

TEST_ZONE_NAME = "example.com"
TEST_ZONE_ID = "zone-123"


# Fixtures for config manager, IP provider, record manager, and zone config
@pytest.fixture
def mock_config_manager():
    manager = Mock()
    manager.load_configuration.return_value = Mock(
        cloudflare_zones=[
            Mock(zone_id=TEST_ZONE_ID, authentication=Mock(), subdomains=[], ttl=300)
        ]
    )
    return manager


@pytest.fixture
def mock_ip_provider():
    provider = Mock()
    provider.get_public_ip.return_value = "1.2.3.4"
    return provider


@pytest.fixture
def mock_record_manager():
    manager = Mock()
    manager.update_all_zones.return_value = [
        Mock(
            zone_name=TEST_ZONE_NAME,
            zone_id=TEST_ZONE_ID,
            total_records=2,
            successful_updates=2,
            failed_updates=0,
            results=[],
            success_rate=100.0,
        )
    ]
    manager.get_overall_summary.return_value = {
        "total_zones": 1,
        "successful_zones": 1,
        "total_records": 2,
        "successful_updates": 2,
        "failed_updates": 0,
    }
    return manager


@pytest.fixture
def mock_validated_zone():
    zone = Mock()
    zone.zone_id = TEST_ZONE_ID
    zone.zone_name = TEST_ZONE_NAME
    zone.authentication = Mock()
    zone.subdomains = []
    zone.ttl = 300
    zone.client = Mock()
    return zone


def test_load_configuration_success(mock_config_manager, mock_ip_provider):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_provider=mock_ip_provider,
    )
    config = updater.load_configuration()
    assert config is not None
    mock_config_manager.load_configuration.assert_called_once()


def test_load_configuration_failure(mock_ip_provider):
    config_manager = Mock()
    config_manager.load_configuration.side_effect = ConfigurationError("Config error")
    updater = DNSUpdater(
        config_manager=config_manager,
        ip_provider=mock_ip_provider,
    )
    with pytest.raises(DNSUpdaterError):
        updater.load_configuration()


def test_validate_zones_success(
    mock_config_manager, mock_ip_provider, mock_validated_zone
):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_provider=mock_ip_provider,
    )
    updater._configuration = Mock(cloudflare_zones=[mock_validated_zone])
    with (
        patch.object(
            updater.authenticator, "create_client", return_value=Mock()
        ) as mock_create_client,
        patch.object(
            updater.validator, "validate_zone", return_value=mock_validated_zone
        ) as mock_validate_zone,
    ):
        zones = updater.validate_zones()
        assert zones == [mock_validated_zone]
        mock_create_client.assert_called_once()
        mock_validate_zone.assert_called_once()


def test_validate_zones_failure(mock_config_manager, mock_ip_provider):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_provider=mock_ip_provider,
    )
    updater._configuration = Mock(
        cloudflare_zones=[
            Mock(zone_id="bad-zone", authentication=Mock(), subdomains=[], ttl=300)
        ]
    )
    with patch.object(
        updater.authenticator,
        "create_client",
        side_effect=AuthenticationError("Auth failed"),
    ):
        with pytest.raises(DNSUpdaterError):
            updater.validate_zones()


def test_get_current_ip_success(mock_config_manager, mock_ip_provider):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_provider=mock_ip_provider,
    )
    ip = updater.get_current_ip()
    assert ip == "1.2.3.4"
    mock_ip_provider.get_public_ip.assert_called_once()


def test_get_current_ip_failure(mock_config_manager):
    ip_provider = Mock()
    ip_provider.get_public_ip.side_effect = IPProviderError("IP error")
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_provider=ip_provider,
    )
    with pytest.raises(DNSUpdaterError):
        updater.get_current_ip()


def test_update_dns_records_success(
    mock_config_manager, mock_ip_provider, mock_record_manager, mock_validated_zone
):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_provider=mock_ip_provider,
        record_manager=mock_record_manager,
    )
    updater._validated_zones = [mock_validated_zone]
    updater._last_known_ip = "0.0.0.0"
    result = updater.update_dns_records("1.2.3.4")
    assert isinstance(result, UpdateCycleResult)
    assert result.successful_updates == 2
    assert result.failed_updates == 0


def test_update_dns_records_failure(
    mock_config_manager, mock_ip_provider, mock_record_manager
):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_provider=mock_ip_provider,
        record_manager=mock_record_manager,
    )
    updater._validated_zones = [Mock()]
    mock_record_manager.update_all_zones.side_effect = Exception("Update error")
    with pytest.raises(DNSUpdaterError):
        updater.update_dns_records("1.2.3.4")


def test_check_and_update_ip_changed(
    mock_config_manager, mock_ip_provider, mock_record_manager, mock_validated_zone
):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_provider=mock_ip_provider,
        record_manager=mock_record_manager,
    )
    updater._validated_zones = [mock_validated_zone]
    updater._last_known_ip = "0.0.0.0"
    with patch.object(
        updater,
        "update_dns_records",
        return_value=Mock(ip_changed=True, successful_updates=2, failed_updates=0),
    ) as mock_update:
        result = updater.check_and_update()
        assert result.ip_changed
        mock_update.assert_called_once_with("1.2.3.4")


def test_check_and_update_no_ip_change(
    mock_config_manager, mock_ip_provider, mock_record_manager, mock_validated_zone
):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_provider=mock_ip_provider,
        record_manager=mock_record_manager,
    )
    updater._validated_zones = [mock_validated_zone]
    updater._last_known_ip = "1.2.3.4"
    result = updater.check_and_update()
    assert not result.ip_changed
    assert result.total_records_updated == 0


def test_run_continuous_keyboard_interrupt(
    mock_config_manager, mock_ip_provider, mock_record_manager, mock_validated_zone
):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_provider=mock_ip_provider,
        record_manager=mock_record_manager,
    )
    updater._validated_zones = [mock_validated_zone]
    with patch.object(updater, "check_and_update", side_effect=KeyboardInterrupt):
        # Should exit gracefully on KeyboardInterrupt
        updater.run_continuous(check_interval=1)


# def test_run_continuous_dns_updater_error(
#     mock_config_manager, mock_ip_provider, mock_record_manager, mock_validated_zone
# ):
#     updater = DNSUpdater(
#         config_manager=mock_config_manager,
#         ip_provider=mock_ip_provider,
#         record_manager=mock_record_manager,
#     )
#     updater._validated_zones = [mock_validated_zone]
#     with (
#         patch.object(
#             updater, "check_and_update", side_effect=DNSUpdaterError("Update error")
#         ),
#         patch("ddns_updater.time.sleep", return_value=None),
#     ):
#         # Should log error and retry
#         updater.run_continuous(check_interval=1)
