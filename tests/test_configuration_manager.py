import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import the modules we're testing
from configuration_manager import (
    AuthenticationConfig,
    CloudflareZoneConfig,
    ConfigurationBuilder,
    ConfigurationError,
    ConfigurationManager,
    ConfigurationParserFactory,
    DDNSConfiguration,
    JSONConfigurationParser,
    SubdomainConfig,
    YAMLConfigurationParser,
    ZoneBuilder,
    create_configuration_manager,
    create_sample_configuration,
    load_configuration_from_file,
)


# Test Fixtures
@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files"""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


@pytest.fixture
def sample_config_dict():
    """Sample configuration dictionary"""
    return {
        "cloudflare": [
            {
                "authentication": {"api_token": "test-token-123"},
                "zone_id": "test-zone-id",
                "ttl": 300,
                "subdomains": [
                    {"name": "@", "proxied": True},
                    {"name": "www", "proxied": False, "ttl": 120},
                ],
            }
        ],
        "ttl": 600,
    }


@pytest.fixture
def sample_yaml_config():
    """Sample YAML configuration content"""
    return """
cloudflare:
  - authentication:
      api_token: test-token-123
    zone_id: test-zone-id
    ttl: 300
    subdomains:
      - name: "@"
        proxied: true
      - name: "www"
        proxied: false
        ttl: 120
ttl: 600
"""


@pytest.fixture
def sample_json_config():
    """Sample JSON configuration content"""
    return """{
  "cloudflare": [
    {
      "authentication": {
        "api_token": "test-token-123"
      },
      "zone_id": "test-zone-id",
      "ttl": 300,
      "subdomains": [
        {
          "name": "@",
          "proxied": true
        },
        {
          "name": "www",
          "proxied": false,
          "ttl": 120
        }
      ]
    }
  ],
  "ttl": 600
}"""


@pytest.fixture
def invalid_yaml_config():
    """Invalid YAML configuration"""
    return """
cloudflare:
  - authentication:
      api_token: test-token
    zone_id: ""  # Invalid empty zone_id
    subdomains: []  # Invalid empty subdomains
"""


@pytest.fixture
def env_vars():
    """Sample environment variables"""
    return {
        "CF_DDNS_API_TOKEN": "env-token-456",
        "CF_DDNS_API_KEY": "env-key-789",
        "CF_DDNS_API_EMAIL": "test@example.com",
    }


# Test Data Classes
class TestSubdomainConfig:
    """Tests for SubdomainConfig"""

    def test_valid_subdomain_config(self):
        """Test creating valid subdomain config"""
        config = SubdomainConfig(name="www", proxied=True, ttl=300)
        assert config.name == "www"
        assert config.proxied is True
        assert config.ttl == 300

    def test_subdomain_config_defaults(self):
        """Test default values"""
        config = SubdomainConfig(name="api")
        assert config.name == "api"
        assert config.proxied is False
        assert config.ttl is None

    def test_invalid_ttl_too_low(self):
        """Test TTL validation - too low"""
        with pytest.raises(ConfigurationError, match="Invalid TTL 30"):
            SubdomainConfig(name="test", ttl=30)

    def test_invalid_ttl_too_high(self):
        """Test TTL validation - too high"""
        with pytest.raises(ConfigurationError, match="Invalid TTL 90000"):
            SubdomainConfig(name="test", ttl=90000)

    def test_valid_ttl_edge_cases(self):
        """Test TTL validation - edge cases"""
        # TTL = 1 is valid (auto)
        config1 = SubdomainConfig(name="test", ttl=1)
        assert config1.ttl == 1

        # TTL = 60 is valid (minimum)
        config2 = SubdomainConfig(name="test", ttl=60)
        assert config2.ttl == 60

        # TTL = 86400 is valid (maximum)
        config3 = SubdomainConfig(name="test", ttl=86400)
        assert config3.ttl == 86400


class TestAuthenticationConfig:
    """Tests for AuthenticationConfig"""

    def test_valid_api_token_auth(self):
        """Test valid API token authentication"""
        config = AuthenticationConfig(api_token="test-token")
        assert config.api_token == "test-token"
        assert config.api_key is None
        assert config.api_email is None

    def test_valid_api_key_auth(self):
        """Test valid API key + email authentication"""
        config = AuthenticationConfig(api_key="test-key", api_email="test@example.com")
        assert config.api_token is None
        assert config.api_key == "test-key"
        assert config.api_email == "test@example.com"

    def test_missing_authentication(self):
        """Test missing authentication"""
        with pytest.raises(ConfigurationError, match="Authentication requires either"):
            AuthenticationConfig()

    def test_both_auth_methods(self):
        """Test providing both authentication methods"""
        with pytest.raises(ConfigurationError, match="Specify either 'api_token' OR"):
            AuthenticationConfig(
                api_token="token", api_key="key", api_email="email@example.com"
            )

    def test_incomplete_api_key_auth(self):
        """Test incomplete API key authentication"""
        with pytest.raises(ConfigurationError, match="Authentication requires either"):
            AuthenticationConfig(api_key="key")  # Missing email


class TestCloudflareZoneConfig:
    """Tests for CloudflareZoneConfig"""

    def test_valid_zone_config(self):
        """Test valid zone configuration"""
        auth = AuthenticationConfig(api_token="token")
        subdomain = SubdomainConfig(name="www")

        config = CloudflareZoneConfig(
            authentication=auth, zone_id="zone123", subdomains=[subdomain], ttl=300
        )

        assert config.zone_id == "zone123"
        assert config.ttl == 300
        assert len(config.subdomains) == 1

    def test_empty_zone_id(self):
        """Test empty zone ID"""
        auth = AuthenticationConfig(api_token="token")
        subdomain = SubdomainConfig(name="www")

        with pytest.raises(ConfigurationError, match="zone_id cannot be empty"):
            CloudflareZoneConfig(
                authentication=auth, zone_id="", subdomains=[subdomain]
            )

    def test_empty_subdomains(self):
        """Test empty subdomains list"""
        auth = AuthenticationConfig(api_token="token")

        with pytest.raises(
            ConfigurationError, match="At least one subdomain must be configured"
        ):
            CloudflareZoneConfig(authentication=auth, zone_id="zone123", subdomains=[])

    def test_get_effective_ttl(self):
        """Test TTL resolution logic"""
        auth = AuthenticationConfig(api_token="token")

        # Subdomain with specific TTL
        sub1 = SubdomainConfig(name="www", ttl=120)
        # Subdomain without TTL
        sub2 = SubdomainConfig(name="api")

        # Zone with default TTL
        zone = CloudflareZoneConfig(
            authentication=auth, zone_id="zone123", subdomains=[sub1, sub2], ttl=300
        )

        # Should use subdomain TTL
        assert zone.get_effective_ttl(sub1) == 120
        # Should use zone TTL
        assert zone.get_effective_ttl(sub2) == 300

    def test_get_effective_ttl_defaults(self):
        """Test TTL resolution with defaults"""
        auth = AuthenticationConfig(api_token="token")
        subdomain = SubdomainConfig(name="www")  # No TTL

        # Zone without TTL
        zone = CloudflareZoneConfig(
            authentication=auth, zone_id="zone123", subdomains=[subdomain]
        )

        # Should use default TTL (300)
        assert zone.get_effective_ttl(subdomain) == 300


class TestDDNSConfiguration:
    """Tests for DDNSConfiguration"""

    def test_valid_configuration(self):
        """Test valid DDNS configuration"""
        auth = AuthenticationConfig(api_token="token")
        subdomain = SubdomainConfig(name="www")
        zone = CloudflareZoneConfig(
            authentication=auth, zone_id="zone123", subdomains=[subdomain]
        )

        config = DDNSConfiguration(cloudflare_zones=[zone], global_ttl=600)

        assert len(config.cloudflare_zones) == 1
        assert config.global_ttl == 600

    def test_empty_zones(self):
        """Test empty zones list"""
        with pytest.raises(
            ConfigurationError, match="At least one Cloudflare zone must be configured"
        ):
            DDNSConfiguration(cloudflare_zones=[])


# Test Configuration Parsers
class TestYAMLConfigurationParser:
    """Tests for YAML parser"""

    def test_parse_valid_yaml(self, sample_yaml_config):
        """Test parsing valid YAML"""
        parser = YAMLConfigurationParser()
        result = parser.parse(sample_yaml_config)

        assert "cloudflare" in result
        assert len(result["cloudflare"]) == 1
        assert result["ttl"] == 600

    def test_parse_invalid_yaml(self):
        """Test parsing invalid YAML"""
        parser = YAMLConfigurationParser()
        invalid_yaml = "invalid: yaml: content: ["

        with pytest.raises(ConfigurationError, match="Invalid YAML"):
            parser.parse(invalid_yaml)

    def test_parse_empty_yaml(self):
        """Test parsing empty YAML"""
        parser = YAMLConfigurationParser()

        with pytest.raises(ConfigurationError, match="Configuration file is empty"):
            parser.parse("")

    def test_get_file_extensions(self):
        """Test file extensions"""
        parser = YAMLConfigurationParser()
        extensions = parser.get_file_extensions()
        assert "yaml" in extensions
        assert "yml" in extensions

    def test_get_format_name(self):
        """Test format name"""
        parser = YAMLConfigurationParser()
        assert parser.get_format_name() == "YAML"


class TestJSONConfigurationParser:
    """Tests for JSON parser"""

    def test_parse_valid_json(self, sample_json_config):
        """Test parsing valid JSON"""
        parser = JSONConfigurationParser()
        result = parser.parse(sample_json_config)

        assert "cloudflare" in result
        assert len(result["cloudflare"]) == 1
        assert result["ttl"] == 600

    def test_parse_invalid_json(self):
        """Test parsing invalid JSON"""
        parser = JSONConfigurationParser()
        invalid_json = '{"invalid": json,}'

        with pytest.raises(ConfigurationError, match="Invalid JSON"):
            parser.parse(invalid_json)

    def test_parse_empty_json(self):
        """Test parsing empty JSON"""
        parser = JSONConfigurationParser()

        with pytest.raises(ConfigurationError, match="Configuration file is empty"):
            parser.parse("null")

    def test_get_file_extensions(self):
        """Test file extensions"""
        parser = JSONConfigurationParser()
        extensions = parser.get_file_extensions()
        assert "json" in extensions

    def test_get_format_name(self):
        """Test format name"""
        parser = JSONConfigurationParser()
        assert parser.get_format_name() == "JSON"


class TestConfigurationParserFactory:
    """Tests for parser factory"""

    def test_create_yaml_parser(self):
        """Test creating YAML parser"""
        parser = ConfigurationParserFactory.create_parser("yaml")
        assert isinstance(parser, YAMLConfigurationParser)

    def test_create_json_parser(self):
        """Test creating JSON parser"""
        parser = ConfigurationParserFactory.create_parser("json")
        assert isinstance(parser, JSONConfigurationParser)

    def test_create_parser_case_insensitive(self):
        """Test case insensitive extension"""
        parser = ConfigurationParserFactory.create_parser("YAML")
        assert isinstance(parser, YAMLConfigurationParser)

    def test_create_parser_with_dot(self):
        """Test extension with leading dot"""
        parser = ConfigurationParserFactory.create_parser(".json")
        assert isinstance(parser, JSONConfigurationParser)

    def test_unsupported_extension(self):
        """Test unsupported file extension"""
        with pytest.raises(
            ConfigurationError, match="Unsupported configuration format: 'toml'"
        ):
            ConfigurationParserFactory.create_parser("toml")

    def test_get_supported_extensions(self):
        """Test getting supported extensions"""
        extensions = ConfigurationParserFactory.get_supported_extensions()
        assert "yaml" in extensions
        assert "yml" in extensions
        assert "json" in extensions

    def test_register_custom_parser(self):
        """Test registering custom parser"""

        class CustomParser(YAMLConfigurationParser):
            def get_file_extensions(self):
                return ["custom"]

            def get_format_name(self):
                return "Custom"

        # Register the parser
        ConfigurationParserFactory.register_parser(CustomParser)

        # Should be able to create it
        parser = ConfigurationParserFactory.create_parser("custom")
        assert isinstance(parser, CustomParser)

        # Should appear in supported extensions
        extensions = ConfigurationParserFactory.get_supported_extensions()
        assert "custom" in extensions


class TestConfigurationManager:
    """Tests for ConfigurationManager"""

    def test_init_default_path(self):
        """Test initialization with default path"""
        manager = ConfigurationManager()
        assert manager.base_path == Path.cwd()

    def test_init_custom_path(self, temp_dir):
        """Test initialization with custom path"""
        manager = ConfigurationManager(temp_dir)
        assert manager.base_path == temp_dir

    @patch.dict(os.environ, {"CF_DDNS_TEST": "value"})
    def test_load_environment_variables(self):
        """Test loading environment variables"""
        manager = ConfigurationManager()
        env_vars = manager._load_environment_variables()
        assert "CF_DDNS_TEST" in env_vars
        assert env_vars["CF_DDNS_TEST"] == "value"

    def test_find_config_file_yaml(self, temp_dir, sample_yaml_config):
        """Test finding YAML config file"""
        config_file = temp_dir / "config.yaml"
        config_file.write_text(sample_yaml_config)

        manager = ConfigurationManager(temp_dir)
        found_file = manager._find_config_file()
        assert found_file == config_file

    def test_find_config_file_json(self, temp_dir, sample_json_config):
        """Test finding JSON config file"""
        config_file = temp_dir / "config.json"
        config_file.write_text(sample_json_config)

        manager = ConfigurationManager(temp_dir)
        found_file = manager._find_config_file()
        assert found_file == config_file

    def test_find_config_file_priority(
        self, temp_dir, sample_yaml_config, sample_json_config
    ):
        """Test config file priority (first supported extension wins)"""
        yaml_file = temp_dir / "config.yaml"
        json_file = temp_dir / "config.json"

        yaml_file.write_text(sample_yaml_config)
        json_file.write_text(sample_json_config)

        manager = ConfigurationManager(temp_dir)
        found_file = manager._find_config_file()

        # Should find the first supported extension
        supported_extensions = ConfigurationParserFactory.get_supported_extensions()
        first_ext = supported_extensions[0]
        expected_file = temp_dir / f"config.{first_ext}"
        assert found_file == expected_file

    def test_find_config_file_not_found(self, temp_dir):
        """Test config file not found"""
        manager = ConfigurationManager(temp_dir)

        with pytest.raises(ConfigurationError, match="Configuration file not found"):
            manager._find_config_file()

    def test_load_configuration_yaml(self, temp_dir, sample_yaml_config):
        """Test loading YAML configuration"""
        config_file = temp_dir / "config.yaml"
        config_file.write_text(sample_yaml_config)

        manager = ConfigurationManager(temp_dir)
        config = manager.load_configuration()

        assert isinstance(config, DDNSConfiguration)
        assert len(config.cloudflare_zones) == 1
        assert config.global_ttl == 600

    def test_load_configuration_json(self, temp_dir, sample_json_config):
        """Test loading JSON configuration"""
        config_file = temp_dir / "config.json"
        config_file.write_text(sample_json_config)

        manager = ConfigurationManager(temp_dir)
        config = manager.load_configuration()

        assert isinstance(config, DDNSConfiguration)
        assert len(config.cloudflare_zones) == 1
        assert config.global_ttl == 600

    def test_load_configuration_with_env_vars(self, temp_dir):
        """Test loading configuration with environment variable substitution"""
        config_content = """
cloudflare:
  - authentication:
      api_token: "${CF_DDNS_API_TOKEN}"
    zone_id: "zone123"
    subdomains:
      - name: "@"
        proxied: true
"""
        config_file = temp_dir / "config.yaml"
        config_file.write_text(config_content)

        with patch.dict(os.environ, {"CF_DDNS_API_TOKEN": "env-token-123"}):
            manager = ConfigurationManager(temp_dir)
            config = manager.load_configuration()

            auth = config.cloudflare_zones[0].authentication
            assert auth.api_token == "env-token-123"

    def test_load_configuration_invalid_schema(self, temp_dir):
        """Test loading configuration with invalid schema"""
        invalid_config = """
cloudflare:
  - zone_id: "zone123"
    # Missing authentication and subdomains
"""
        config_file = temp_dir / "config.yaml"
        config_file.write_text(invalid_config)

        manager = ConfigurationManager(temp_dir)

        with pytest.raises(ConfigurationError, match="Configuration validation failed"):
            manager.load_configuration()

    def test_get_configuration_not_loaded(self):
        """Test getting configuration before loading"""
        manager = ConfigurationManager()

        with pytest.raises(ConfigurationError, match="Configuration not loaded"):
            manager.get_configuration()

    def test_get_configuration_after_load(self, temp_dir, sample_yaml_config):
        """Test getting configuration after loading"""
        config_file = temp_dir / "config.yaml"
        config_file.write_text(sample_yaml_config)

        manager = ConfigurationManager(temp_dir)
        config1 = manager.load_configuration()
        config2 = manager.get_configuration()

        assert config1 is config2  # Should be the same cached instance

    def test_reload_configuration(self, temp_dir, sample_yaml_config):
        """Test reloading configuration"""
        config_file = temp_dir / "config.yaml"
        config_file.write_text(sample_yaml_config)

        manager = ConfigurationManager(temp_dir)
        config1 = manager.load_configuration()

        # Modify the file
        modified_config = sample_yaml_config.replace("ttl: 600", "ttl: 900")
        config_file.write_text(modified_config)

        config2 = manager.reload_configuration()

        assert config1 is not config2  # Should be different instances
        assert config2.global_ttl == 900

    def test_get_supported_formats(self):
        """Test getting supported formats"""
        manager = ConfigurationManager()
        formats = manager.get_supported_formats()

        assert "yaml" in formats
        assert "json" in formats
        assert formats["yaml"] == "YAML"
        assert formats["json"] == "JSON"


class TestConfigurationBuilder:
    """Tests for ConfigurationBuilder"""

    def test_basic_builder_usage(self):
        """Test basic builder pattern usage"""
        config = (
            ConfigurationBuilder()
            .set_global_ttl(300)
            .add_zone("zone123", api_token="token123")
            .add_subdomain("www", proxied=True)
            .add_subdomain("api", proxied=False, ttl=120)
            .done()
            .build()
        )

        assert config.global_ttl == 300
        assert len(config.cloudflare_zones) == 1

        zone = config.cloudflare_zones[0]
        assert zone.zone_id == "zone123"
        assert zone.authentication.api_token == "token123"
        assert len(zone.subdomains) == 2

        # Check subdomains
        www_sub = next(s for s in zone.subdomains if s.name == "www")
        assert www_sub.proxied is True

        api_sub = next(s for s in zone.subdomains if s.name == "api")
        assert api_sub.proxied is False
        assert api_sub.ttl == 120

    def test_multiple_zones(self):
        """Test builder with multiple zones"""
        config = (
            ConfigurationBuilder()
            .add_zone("zone1", api_token="token1")
            .add_subdomain("www")
            .done()
            .add_zone("zone2", api_key="key2", api_email="email@example.com")
            .add_subdomain("api")
            .done()
            .build()
        )

        assert len(config.cloudflare_zones) == 2

        zone1 = config.cloudflare_zones[0]
        assert zone1.zone_id == "zone1"
        assert zone1.authentication.api_token == "token1"

        zone2 = config.cloudflare_zones[1]
        assert zone2.zone_id == "zone2"
        assert zone2.authentication.api_key == "key2"

    def test_builder_validation(self):
        """Test that builder still validates configurations"""
        with pytest.raises(ConfigurationError):
            (
                ConfigurationBuilder()
                .add_zone("zone1")  # Missing authentication
                .add_subdomain("www")
                .done()
                .build()
            )


class TestZoneBuilder:
    """Tests for ZoneBuilder"""

    def test_zone_builder_methods(self):
        """Test zone builder method chaining"""
        builder = ConfigurationBuilder()
        zone_builder = builder.add_zone("zone123", api_token="token")

        # Should return ZoneBuilder
        assert isinstance(zone_builder, ZoneBuilder)

        # Should be able to chain subdomain additions
        result = zone_builder.add_subdomain("www", proxied=True).add_subdomain(
            "api", ttl=120
        )

        assert isinstance(result, ZoneBuilder)

        # Should return to ConfigurationBuilder
        config_builder = result.done()
        assert isinstance(config_builder, ConfigurationBuilder)


class TestFactoryFunctions:
    """Tests for factory functions"""

    def test_create_configuration_manager(self, temp_dir):
        """Test create_configuration_manager factory"""
        manager = create_configuration_manager(temp_dir)
        assert isinstance(manager, ConfigurationManager)
        assert manager.base_path == temp_dir

    def test_load_configuration_from_file(self, temp_dir, sample_yaml_config):
        """Test load_configuration_from_file factory"""
        config_file = temp_dir / "config.yaml"
        config_file.write_text(sample_yaml_config)

        with patch("configuration_manager.ConfigurationManager") as mock_manager_class:
            mock_manager = MagicMock()
            mock_config = MagicMock()
            mock_manager.load_configuration.return_value = mock_config
            mock_manager_class.return_value = mock_manager

            result = load_configuration_from_file(config_file)

            mock_manager_class.assert_called_once()
            mock_manager.load_configuration.assert_called_once_with(config_file)
            assert result == mock_config


class TestSampleConfiguration:
    """Tests for sample configuration generation"""

    def test_create_sample_yaml(self):
        """Test creating sample YAML configuration"""
        sample = create_sample_configuration("yaml")
        assert isinstance(sample, str)
        assert "cloudflare:" in sample
        assert "authentication:" in sample
        assert "# " in sample  # Should have comments

    def test_create_sample_json(self):
        """Test creating sample JSON configuration"""
        sample = create_sample_configuration("json")
        assert isinstance(sample, str)

        # Should be valid JSON
        config = json.loads(sample)
        assert "cloudflare" in config
        assert "authentication" in config["cloudflare"][0]

    def test_create_sample_default_format(self):
        """Test creating sample with default format"""
        sample = create_sample_configuration()
        assert isinstance(sample, str)
        # Default should be YAML (with comments)
        assert "# " in sample
