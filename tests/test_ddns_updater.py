from unittest.mock import Mock, patch

import pytest

from configuration_manager import ConfigurationError
from ddns_updater import (
    AuthenticationError,
    CloudflareAuthenticator,
    DNSUpdater,
    DNSUpdaterError,
    UpdateCycleResult,
    ZoneValidationError,
    ZoneValidator,
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
def mock_ip_providers():
    provider = Mock()
    provider.get_public_ip.return_value = "1.2.3.4"
    return {"A": provider}


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
    subdomain = Mock()
    subdomain.record_types = ["A"]
    zone.subdomains = [subdomain]
    zone.ttl = 300
    zone.client = Mock()
    return zone


def test_load_configuration_success(mock_config_manager, mock_ip_providers):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_providers=mock_ip_providers,
    )
    config = updater.load_configuration()
    assert config is not None
    mock_config_manager.load_configuration.assert_called_once()


def test_load_configuration_failure(mock_ip_providers):
    config_manager = Mock()
    config_manager.load_configuration.side_effect = ConfigurationError("Config error")
    updater = DNSUpdater(
        config_manager=config_manager,
        ip_providers=mock_ip_providers,
    )
    with pytest.raises(DNSUpdaterError):
        updater.load_configuration()


def test_validate_zones_success(
    mock_config_manager, mock_ip_providers, mock_validated_zone
):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_providers=mock_ip_providers,
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


def test_validate_zones_failure(mock_config_manager, mock_ip_providers):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_providers=mock_ip_providers,
    )
    updater._configuration = Mock(
        cloudflare_zones=[
            Mock(zone_id="bad-zone", authentication=Mock(), subdomains=[], ttl=300)
        ]
    )
    with (
        patch.object(
            updater.authenticator,
            "create_client",
            side_effect=AuthenticationError("Auth failed"),
        ),
        pytest.raises(DNSUpdaterError),
    ):
        updater.validate_zones()


def test_get_current_ips_success(
    mock_config_manager, mock_ip_providers, mock_validated_zone
):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_providers=mock_ip_providers,
    )
    updater._validated_zones = [mock_validated_zone]
    ips = updater.get_current_ips()
    assert ips == {"A": "1.2.3.4"}
    mock_ip_providers["A"].get_public_ip.assert_called_once()


def test_get_current_ips_ipv4_failure_raises(mock_config_manager, mock_validated_zone):
    provider = Mock()
    provider.get_public_ip.side_effect = IPProviderError("IP error")
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_providers={"A": provider},
    )
    updater._validated_zones = [mock_validated_zone]
    with pytest.raises(DNSUpdaterError):
        updater.get_current_ips()


def test_get_current_ips_ipv6_failure_skips(mock_config_manager):
    v4 = Mock()
    v4.get_public_ip.return_value = "1.2.3.4"
    v6 = Mock()
    v6.get_public_ip.side_effect = IPProviderError("no IPv6")

    zone = Mock()
    subdomain = Mock()
    subdomain.record_types = ["A", "AAAA"]
    zone.subdomains = [subdomain]

    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_providers={"A": v4, "AAAA": v6},
    )
    updater._validated_zones = [zone]
    ips = updater.get_current_ips()
    assert ips == {"A": "1.2.3.4"}  # AAAA skipped gracefully


def test_get_current_ips_only_fetches_needed_families(mock_config_manager):
    v4 = Mock()
    v4.get_public_ip.return_value = "1.2.3.4"
    v6 = Mock()
    v6.get_public_ip.return_value = "2001:db8::1"

    zone = Mock()
    subdomain = Mock()
    subdomain.record_types = ["A"]  # no subdomain requests AAAA
    zone.subdomains = [subdomain]

    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_providers={"A": v4, "AAAA": v6},
    )
    updater._validated_zones = [zone]
    ips = updater.get_current_ips()
    assert ips == {"A": "1.2.3.4"}
    v6.get_public_ip.assert_not_called()


def test_update_dns_records_success(
    mock_config_manager, mock_ip_providers, mock_record_manager, mock_validated_zone
):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_providers=mock_ip_providers,
        record_manager=mock_record_manager,
    )
    updater._validated_zones = [mock_validated_zone]
    updater._last_known_ips = {"A": "0.0.0.0"}
    result = updater.update_dns_records({"A": "1.2.3.4"})
    assert isinstance(result, UpdateCycleResult)
    assert result.successful_updates == 2
    assert result.failed_updates == 0


def test_update_dns_records_failure(
    mock_config_manager, mock_ip_providers, mock_record_manager
):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_providers=mock_ip_providers,
        record_manager=mock_record_manager,
    )
    updater._validated_zones = [Mock()]
    mock_record_manager.update_all_zones.side_effect = Exception("Update error")
    with pytest.raises(DNSUpdaterError):
        updater.update_dns_records({"A": "1.2.3.4"})


def test_check_and_update_ip_changed(
    mock_config_manager, mock_ip_providers, mock_record_manager, mock_validated_zone
):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_providers=mock_ip_providers,
        record_manager=mock_record_manager,
    )
    updater._validated_zones = [mock_validated_zone]
    updater._last_known_ips = {"A": "0.0.0.0"}
    with patch.object(
        updater,
        "update_dns_records",
        return_value=Mock(ip_changed=True, successful_updates=2, failed_updates=0),
    ) as mock_update:
        result = updater.check_and_update()
        assert result.ip_changed
        mock_update.assert_called_once_with({"A": "1.2.3.4"})


def test_check_and_update_no_ip_change(
    mock_config_manager, mock_ip_providers, mock_record_manager, mock_validated_zone
):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_providers=mock_ip_providers,
        record_manager=mock_record_manager,
    )
    updater._validated_zones = [mock_validated_zone]
    updater._last_known_ips = {"A": "1.2.3.4"}
    result = updater.check_and_update()
    assert not result.ip_changed
    assert result.total_records_updated == 0


def test_run_continuous_keyboard_interrupt(
    mock_config_manager, mock_ip_providers, mock_record_manager, mock_validated_zone
):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_providers=mock_ip_providers,
        record_manager=mock_record_manager,
    )
    updater._validated_zones = [mock_validated_zone]
    with patch.object(updater, "check_and_update", side_effect=KeyboardInterrupt):
        # Should exit gracefully on KeyboardInterrupt
        updater.run_continuous(check_interval=1)


def test_run_continuous_dns_updater_error(
    mock_config_manager, mock_ip_providers, mock_record_manager, mock_validated_zone
):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_providers=mock_ip_providers,
        record_manager=mock_record_manager,
    )
    updater._validated_zones = [mock_validated_zone]
    with (
        patch.object(
            updater,
            "check_and_update",
            side_effect=[DNSUpdaterError("Update error"), KeyboardInterrupt],
        ),
        patch("ddns_updater.time.sleep", return_value=None),
    ):
        # Should log error, retry, then exit on KeyboardInterrupt
        updater.run_continuous(check_interval=1)


def test_run_continuous_unexpected_error(
    mock_config_manager, mock_ip_providers, mock_record_manager, mock_validated_zone
):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_providers=mock_ip_providers,
        record_manager=mock_record_manager,
    )
    updater._validated_zones = [mock_validated_zone]
    with (
        patch.object(
            updater,
            "check_and_update",
            side_effect=[RuntimeError("boom"), KeyboardInterrupt],
        ),
        patch("ddns_updater.time.sleep", return_value=None),
    ):
        # Should log unexpected error, retry, then exit on KeyboardInterrupt
        updater.run_continuous(check_interval=1)


def test_run_continuous_not_initialized(mock_config_manager, mock_ip_providers):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_providers=mock_ip_providers,
    )
    with pytest.raises(DNSUpdaterError):
        updater.run_continuous(check_interval=1)


@pytest.mark.parametrize("failed_updates", [0, 1])
def test_run_continuous_ip_changed_branches(
    mock_config_manager,
    mock_ip_providers,
    mock_record_manager,
    mock_validated_zone,
    failed_updates,
):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_providers=mock_ip_providers,
        record_manager=mock_record_manager,
    )
    updater._validated_zones = [mock_validated_zone]
    result = Mock(ip_changed=True, failed_updates=failed_updates)
    with (
        patch.object(
            updater, "check_and_update", side_effect=[result, KeyboardInterrupt]
        ),
        patch("ddns_updater.time.sleep", return_value=None),
    ):
        updater.run_continuous(check_interval=1)


# UpdateCycleResult properties / string representation


def test_update_cycle_result_success_rate_no_records():
    result = UpdateCycleResult(
        ip_addresses={"A": "1.2.3.4"},
        ip_changed=False,
        zones_processed=0,
        total_records_updated=0,
        successful_updates=0,
        failed_updates=0,
        zone_summaries=[],
        execution_time_seconds=0.0,
    )
    assert result.success_rate == 100.0
    assert "No IP change detected" in str(result)


def test_update_cycle_result_str_ip_changed():
    result = UpdateCycleResult(
        ip_addresses={"A": "1.2.3.4", "AAAA": "2001:db8::1"},
        ip_changed=True,
        zones_processed=1,
        total_records_updated=4,
        successful_updates=3,
        failed_updates=1,
        zone_summaries=[],
        execution_time_seconds=1.5,
    )
    assert result.success_rate == 75.0
    assert "A=1.2.3.4" in str(result)
    assert "AAAA=2001:db8::1" in str(result)


# CloudflareAuthenticator.create_client


@pytest.fixture(autouse=True)
def _clear_cf_env(monkeypatch):
    for var in ("CF_DDNS_API_TOKEN", "CF_DDNS_API_KEY", "CF_DDNS_API_EMAIL"):
        monkeypatch.delenv(var, raising=False)


def test_create_client_env_token(monkeypatch):
    monkeypatch.setenv("CF_DDNS_API_TOKEN", "env-token")
    with patch("ddns_updater.Cloudflare") as mock_cf:
        CloudflareAuthenticator.create_client(Mock())
    mock_cf.assert_called_once_with(api_token="env-token")


def test_create_client_env_key_email(monkeypatch):
    monkeypatch.setenv("CF_DDNS_API_KEY", "env-key")
    monkeypatch.setenv("CF_DDNS_API_EMAIL", "env@example.com")
    with patch("ddns_updater.Cloudflare") as mock_cf:
        CloudflareAuthenticator.create_client(Mock())
    mock_cf.assert_called_once_with(api_key="env-key", api_email="env@example.com")


def test_create_client_config_token():
    auth = Mock(api_token="cfg-token", api_key=None, api_email=None)
    with patch("ddns_updater.Cloudflare") as mock_cf:
        CloudflareAuthenticator.create_client(auth)
    mock_cf.assert_called_once_with(api_token="cfg-token")


def test_create_client_config_key_email():
    auth = Mock(api_token=None, api_key="cfg-key", api_email="cfg@example.com")
    with patch("ddns_updater.Cloudflare") as mock_cf:
        CloudflareAuthenticator.create_client(auth)
    mock_cf.assert_called_once_with(api_key="cfg-key", api_email="cfg@example.com")


def test_create_client_invalid_config():
    auth = Mock(api_token=None, api_key=None, api_email=None)
    with pytest.raises(AuthenticationError):
        CloudflareAuthenticator.create_client(auth)


def test_create_client_wraps_unexpected_error():
    auth = Mock(api_token="cfg-token", api_key=None, api_email=None)
    with (
        patch("ddns_updater.Cloudflare", side_effect=RuntimeError("boom")),
        pytest.raises(AuthenticationError),
    ):
        CloudflareAuthenticator.create_client(auth)


# ZoneValidator.validate_zone


def test_validate_zone_success():
    cf = Mock()
    cf.zones.get.return_value = Mock(name="zone-obj")
    cf.zones.get.return_value.name = TEST_ZONE_NAME
    zone_config = Mock(
        authentication=Mock(),
        zone_id=TEST_ZONE_ID,
        subdomains=[Mock()],
        ttl=300,
    )
    validated = ZoneValidator.validate_zone(cf, zone_config)
    assert validated.zone_name == TEST_ZONE_NAME
    assert validated.client is cf


def test_validate_zone_failure():
    cf = Mock()
    cf.zones.get.side_effect = RuntimeError("not found")
    zone_config = Mock(
        authentication=Mock(),
        zone_id=TEST_ZONE_ID,
        subdomains=[],
        ttl=300,
    )
    with pytest.raises(ZoneValidationError):
        ZoneValidator.validate_zone(cf, zone_config)


# validate_zones edge cases


def test_validate_zones_not_loaded(mock_config_manager, mock_ip_providers):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_providers=mock_ip_providers,
    )
    with pytest.raises(DNSUpdaterError):
        updater.validate_zones()


def test_validate_zones_unexpected_error(mock_config_manager, mock_ip_providers):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_providers=mock_ip_providers,
    )
    updater._configuration = Mock(
        cloudflare_zones=[Mock(zone_id="bad", authentication=Mock())]
    )
    with (
        patch.object(
            updater.authenticator, "create_client", side_effect=RuntimeError("boom")
        ),
        pytest.raises(DNSUpdaterError),
    ):
        updater.validate_zones()


# initialize


def test_initialize_success(mock_config_manager, mock_ip_providers):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_providers=mock_ip_providers,
    )
    with (
        patch.object(updater, "load_configuration"),
        patch.object(updater, "validate_zones"),
    ):
        updater.initialize()


def test_initialize_reraises_dns_updater_error(mock_config_manager, mock_ip_providers):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_providers=mock_ip_providers,
    )
    with (
        patch.object(
            updater, "load_configuration", side_effect=DNSUpdaterError("fail")
        ),
        pytest.raises(DNSUpdaterError),
    ):
        updater.initialize()


def test_initialize_wraps_unexpected_error(mock_config_manager, mock_ip_providers):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_providers=mock_ip_providers,
    )
    with (
        patch.object(updater, "load_configuration", side_effect=RuntimeError("boom")),
        pytest.raises(DNSUpdaterError),
    ):
        updater.initialize()


# update_dns_records edge cases


def test_update_dns_records_no_zones(mock_config_manager, mock_ip_providers):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_providers=mock_ip_providers,
    )
    with pytest.raises(DNSUpdaterError):
        updater.update_dns_records("1.2.3.4")


def test_update_dns_records_with_failures(
    mock_config_manager, mock_ip_providers, mock_record_manager, mock_validated_zone
):
    mock_record_manager.get_overall_summary.return_value = {
        "total_zones": 1,
        "successful_zones": 0,
        "total_records": 2,
        "successful_updates": 1,
        "failed_updates": 1,
    }
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_providers=mock_ip_providers,
        record_manager=mock_record_manager,
    )
    updater._validated_zones = [mock_validated_zone]
    updater._last_known_ips = {"A": "0.0.0.0"}
    result = updater.update_dns_records({"A": "1.2.3.4"})
    assert result.failed_updates == 1


# check_and_update error handling


def test_check_and_update_reraises_dns_updater_error(
    mock_config_manager, mock_ip_providers
):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_providers=mock_ip_providers,
    )
    with (
        patch.object(
            updater, "get_current_ips", side_effect=DNSUpdaterError("ip fail")
        ),
        pytest.raises(DNSUpdaterError),
    ):
        updater.check_and_update()


def test_check_and_update_wraps_unexpected_error(
    mock_config_manager, mock_ip_providers
):
    updater = DNSUpdater(
        config_manager=mock_config_manager,
        ip_providers=mock_ip_providers,
    )
    with (
        patch.object(updater, "get_current_ips", side_effect=RuntimeError("boom")),
        pytest.raises(DNSUpdaterError),
    ):
        updater.check_and_update()
