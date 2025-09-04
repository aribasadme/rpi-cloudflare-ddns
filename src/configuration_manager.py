"""
Configuration management module - handles loading, validation, and access to configuration
"""

import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Any, Dict, List, Optional, Type

import yaml
from schema import And, Or, Schema, SchemaError, Use
from schema import Optional as SchemaOptional

logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    """Custom exception for configuration-related errors"""

    pass


@dataclass
class SubdomainConfig:
    """Configuration for a single subdomain"""

    name: str
    proxied: bool = False
    ttl: Optional[int] = None

    def __post_init__(self):
        """Validate subdomain configuration after initialization"""
        if self.ttl is not None:
            if not (self.ttl == 1 or (60 <= self.ttl <= 86400)):
                raise ConfigurationError(
                    f"Invalid TTL {self.ttl} for subdomain '{self.name}'. Must be 1 or between 60-86400"
                )


@dataclass
class AuthenticationConfig:
    """Configuration for Cloudflare authentication"""

    api_token: Optional[str] = None
    api_key: Optional[str] = None
    api_email: Optional[str] = None

    def __post_init__(self):
        """Validate authentication configuration"""
        has_token = bool(self.api_token)
        has_key_email = bool(self.api_key and self.api_email)

        if not (has_token or has_key_email):
            raise ConfigurationError(
                "Authentication requires either 'api_token' or both 'api_key' and 'api_email'"
            )

        if has_token and has_key_email:
            raise ConfigurationError(
                "Specify either 'api_token' OR 'api_key'+'api_email', not both"
            )


@dataclass
class CloudflareZoneConfig:
    """Configuration for a single Cloudflare zone"""

    authentication: AuthenticationConfig
    zone_id: str
    subdomains: List[SubdomainConfig]
    ttl: Optional[int] = None  # Zone-level default TTL

    # Runtime fields (populated during validation)
    zone_name: Optional[str] = field(default=None, init=False)
    client: Optional[Any] = field(default=None, init=False)

    def __post_init__(self):
        """Validate zone configuration"""
        if not self.zone_id.strip():
            raise ConfigurationError("zone_id cannot be empty")

        if not self.subdomains:
            raise ConfigurationError("At least one subdomain must be configured")

        if self.ttl is not None:
            if not (self.ttl == 1 or (60 <= self.ttl <= 86400)):
                raise ConfigurationError(
                    f"Invalid zone TTL {self.ttl}. Must be 1 or between 60-86400"
                )

    def get_effective_ttl(self, subdomain: SubdomainConfig) -> int:
        """Get the effective TTL for a subdomain (subdomain > zone > default)"""
        if subdomain.ttl is not None:
            return subdomain.ttl
        if self.ttl is not None:
            return self.ttl
        return 300  # Default TTL


@dataclass
class DDNSConfiguration:
    """Main DDNS configuration"""

    cloudflare_zones: List[CloudflareZoneConfig]
    global_ttl: Optional[int] = None

    def __post_init__(self):
        """Validate main configuration"""
        if not self.cloudflare_zones:
            raise ConfigurationError("At least one Cloudflare zone must be configured")

        if self.global_ttl is not None:
            if not (self.global_ttl == 1 or (60 <= self.global_ttl <= 86400)):
                raise ConfigurationError(
                    f"Invalid global TTL {self.global_ttl}. Must be 1 or between 60-86400"
                )


# Abstract base class for configuration parsers
class ConfigurationParser(ABC):
    """Abstract base class for configuration parsers"""

    @abstractmethod
    def parse(self, content: str) -> Dict[str, Any]:
        """Parse configuration content and return dictionary"""
        pass

    @abstractmethod
    def get_file_extensions(self) -> List[str]:
        """Return list of supported file extensions"""
        pass

    @abstractmethod
    def get_format_name(self) -> str:
        """Return human-readable format name"""
        pass


class YAMLConfigurationParser(ConfigurationParser):
    """YAML configuration parser"""

    def parse(self, content: str) -> Dict[str, Any]:
        """Parse YAML content"""
        try:
            config = yaml.safe_load(content)
            if config is None:
                raise ConfigurationError("Configuration file is empty")
            return config
        except yaml.YAMLError as e:
            raise ConfigurationError(f"Invalid YAML: {e}")

    def get_file_extensions(self) -> List[str]:
        return ["yaml", "yml"]

    def get_format_name(self) -> str:
        return "YAML"


class JSONConfigurationParser(ConfigurationParser):
    """JSON configuration parser"""

    def parse(self, content: str) -> Dict[str, Any]:
        """Parse JSON content"""
        try:
            config = json.loads(content)
            if config is None:
                raise ConfigurationError("Configuration file is empty")
            return config
        except json.JSONDecodeError as e:
            raise ConfigurationError(f"Invalid JSON: {e}")

    def get_file_extensions(self) -> List[str]:
        return ["json"]

    def get_format_name(self) -> str:
        return "JSON"


class ConfigurationParserFactory:
    """Factory for creating configuration parsers"""

    _parsers: Dict[str, Type[ConfigurationParser]] = {}
    _default_parsers_registered = False

    @classmethod
    def register_parser(cls, parser_class: Type[ConfigurationParser]) -> None:
        """Register a configuration parser"""
        parser_instance = parser_class()
        for ext in parser_instance.get_file_extensions():
            cls._parsers[ext.lower()] = parser_class

    @classmethod
    def _register_default_parsers(cls) -> None:
        """Register default parsers (YAML, JSON)"""
        if not cls._default_parsers_registered:
            cls.register_parser(YAMLConfigurationParser)
            cls.register_parser(JSONConfigurationParser)
            cls._default_parsers_registered = True

    @classmethod
    def create_parser(cls, file_extension: str) -> ConfigurationParser:
        """Create a parser for the given file extension"""
        cls._register_default_parsers()

        ext = file_extension.lower().lstrip(".")
        parser_class = cls._parsers.get(ext)

        if parser_class is None:
            supported_exts = list(cls._parsers.keys())
            raise ConfigurationError(
                f"Unsupported configuration format: '{ext}'. "
                f"Supported formats: {', '.join(supported_exts)}"
            )

        return parser_class()

    @classmethod
    def get_supported_extensions(cls) -> List[str]:
        """Get list of all supported file extensions"""
        cls._register_default_parsers()
        return list(cls._parsers.keys())


class ConfigurationManager:
    """Manages DDNS configuration loading, validation, and access"""

    # Schema for validating raw structure (same for both YAML and JSON)
    CONFIG_SCHEMA = Schema(
        {
            "cloudflare": [
                {
                    "authentication": Or(
                        {"api_token": str},  # type: ignore
                        {"api_key": str, "api_email": str},  # type: ignore
                    ),
                    "zone_id": str,
                    "subdomains": [
                        {
                            "name": str,
                            SchemaOptional("proxied"): bool,
                            SchemaOptional("ttl"): And(
                                Use(int),  # type: ignore
                                lambda n: (n == 1 or (60 <= n <= 86400)),
                            ),
                        }
                    ],
                    SchemaOptional("ttl"): And(
                        Use(int),  # type: ignore
                        lambda n: n == 1 or (60 <= n <= 86400),
                    ),
                }
            ],
            SchemaOptional("ttl"): And(
                Use(int),  # type: ignore
                lambda n: n == 1 or (60 <= n <= 86400),
            ),
        }
    )

    def __init__(self, base_path: Optional[Path] = None):
        """Initialize configuration manager

        Args:
            base_path: Base path to search for config files. Defaults to current working directory.
        """
        self.base_path = base_path or Path.cwd()
        self._configuration: Optional[DDNSConfiguration] = None
        self._env_vars = self._load_environment_variables()
        self._parser_factory = ConfigurationParserFactory()

    def _load_environment_variables(self) -> Dict[str, str]:
        """Load environment variables starting with CF_DDNS_"""
        return {
            key: value
            for key, value in os.environ.items()
            if key.startswith("CF_DDNS_")
        }

    def _find_config_file(self) -> Path:
        """Find configuration file in base path"""
        supported_extensions = self._parser_factory.get_supported_extensions()

        for ext in supported_extensions:
            config_path = self.base_path / f"config.{ext}"
            if config_path.exists():
                logger.info(f"Found configuration file: {config_path}")
                return config_path

        # If no config file found, list what we tried
        tried_files = [f"config.{ext}" for ext in supported_extensions]
        raise ConfigurationError(
            f"Configuration file not found in {self.base_path}. "
            f"Tried: {', '.join(tried_files)}"
        )

    def _load_and_parse_file(self, config_path: Path) -> Dict[str, Any]:
        """Load file and parse with appropriate parser"""
        try:
            # Read file content
            with open(config_path, "r", encoding="utf-8") as config_file:
                content = config_file.read()

            # Substitute environment variables
            template = Template(content)
            substituted_content = template.safe_substitute(self._env_vars)

            # Get appropriate parser based on file extension
            file_extension = config_path.suffix
            parser = self._parser_factory.create_parser(file_extension)

            # Parse content
            raw_config = parser.parse(substituted_content)

            logger.info(
                f"Loaded {parser.get_format_name()} configuration from {config_path}"
            )
            return raw_config

        except ConfigurationError:
            raise
        except Exception as e:
            raise ConfigurationError(f"Error reading {config_path}: {e}")

    def _validate_schema(self, raw_config: Dict[str, Any]) -> None:
        """Validate raw config against schema"""
        try:
            self.CONFIG_SCHEMA.validate(raw_config)
        except SchemaError as e:
            raise ConfigurationError(f"Configuration validation failed: {e}")

    def _convert_to_dataclasses(self, raw_config: Dict[str, Any]) -> DDNSConfiguration:
        """Convert raw config dict to typed dataclasses"""
        try:
            cloudflare_zones = []

            for zone_data in raw_config["cloudflare"]:
                # Parse authentication
                auth_data = zone_data["authentication"]
                auth_config = AuthenticationConfig(
                    api_token=auth_data.get("api_token"),
                    api_key=auth_data.get("api_key"),
                    api_email=auth_data.get("api_email"),
                )

                # Parse subdomains
                subdomains = []
                for sub_data in zone_data["subdomains"]:
                    subdomain = SubdomainConfig(
                        name=sub_data["name"],
                        proxied=sub_data.get("proxied", False),
                        ttl=sub_data.get("ttl"),
                    )
                    subdomains.append(subdomain)

                # Parse zone
                zone_config = CloudflareZoneConfig(
                    authentication=auth_config,
                    zone_id=zone_data["zone_id"],
                    subdomains=subdomains,
                    ttl=zone_data.get("ttl"),
                )
                cloudflare_zones.append(zone_config)

            # Create main configuration
            return DDNSConfiguration(
                cloudflare_zones=cloudflare_zones, global_ttl=raw_config.get("ttl")
            )

        except (KeyError, TypeError) as e:
            raise ConfigurationError(f"Error converting configuration: {e}")

    def load_configuration(
        self, config_path: Optional[Path] = None
    ) -> DDNSConfiguration:
        """Load configuration from file

        Args:
            config_path: Specific config file path. If None, searches in base_path.

        Returns:
            DDNSConfiguration: Loaded and validated configuration

        Raises:
            ConfigurationError: If configuration is invalid or cannot be loaded
        """
        try:
            # Find config file
            if config_path is None:
                config_path = self._find_config_file()

            # Load and parse file
            raw_config = self._load_and_parse_file(config_path)

            # Validate schema
            self._validate_schema(raw_config)

            # Convert to dataclasses
            configuration = self._convert_to_dataclasses(raw_config)

            # Cache the configuration
            self._configuration = configuration

            logger.info(
                f"Successfully loaded configuration with {len(configuration.cloudflare_zones)} zones"
            )
            return configuration

        except ConfigurationError:
            raise
        except Exception as e:
            raise ConfigurationError(f"Unexpected error loading configuration: {e}")

    def get_configuration(self) -> DDNSConfiguration:
        """Get the cached configuration

        Returns:
            DDNSConfiguration: The loaded configuration

        Raises:
            ConfigurationError: If no configuration has been loaded yet
        """
        if self._configuration is None:
            raise ConfigurationError(
                "Configuration not loaded. Call load_configuration() first."
            )
        return self._configuration

    def reload_configuration(self) -> DDNSConfiguration:
        """Reload configuration from file"""
        self._configuration = None
        return self.load_configuration()

    def get_supported_formats(self) -> Dict[str, str]:
        """Get supported configuration formats"""
        formats = {}
        for ext in self._parser_factory.get_supported_extensions():
            try:
                parser = self._parser_factory.create_parser(ext)
                formats[ext] = parser.get_format_name()
            except ConfigurationError:
                continue
        return formats


class ConfigurationBuilder:
    """Builder pattern for creating configurations programmatically"""

    def __init__(self):
        self._zones: List[CloudflareZoneConfig] = []
        self._global_ttl: Optional[int] = None

    def add_zone(
        self,
        zone_id: str,
        api_token: Optional[str] = None,
        api_key: Optional[str] = None,
        api_email: Optional[str] = None,
        zone_ttl: Optional[int] = None,
    ) -> "ZoneBuilder":
        """Add a zone and return a builder for that zone"""

        auth_config = AuthenticationConfig(
            api_token=api_token, api_key=api_key, api_email=api_email
        )

        zone_builder = ZoneBuilder(self, zone_id, auth_config, zone_ttl)
        return zone_builder

    def set_global_ttl(self, ttl: int) -> "ConfigurationBuilder":
        """Set global TTL"""
        self._global_ttl = ttl
        return self

    def build(self) -> DDNSConfiguration:
        """Build the final configuration"""
        return DDNSConfiguration(
            cloudflare_zones=self._zones.copy(), global_ttl=self._global_ttl
        )


class ZoneBuilder:
    """Builder for individual zones"""

    def __init__(
        self,
        parent: ConfigurationBuilder,
        zone_id: str,
        auth: AuthenticationConfig,
        zone_ttl: Optional[int],
    ):
        self._parent = parent
        self._zone_id = zone_id
        self._auth = auth
        self._zone_ttl = zone_ttl
        self._subdomains: List[SubdomainConfig] = []

    def add_subdomain(
        self, name: str, proxied: bool = False, ttl: Optional[int] = None
    ) -> "ZoneBuilder":
        """Add a subdomain to this zone"""
        subdomain = SubdomainConfig(name=name, proxied=proxied, ttl=ttl)
        self._subdomains.append(subdomain)
        return self

    def done(self) -> ConfigurationBuilder:
        """Finish building this zone and return to main builder"""
        zone_config = CloudflareZoneConfig(
            authentication=self._auth,
            zone_id=self._zone_id,
            subdomains=self._subdomains,
            ttl=self._zone_ttl,
        )
        self._parent._zones.append(zone_config)
        return self._parent


# Factory functions for common use cases
def create_configuration_manager(
    base_path: Optional[Path] = None,
) -> ConfigurationManager:
    """Create a configuration manager with default settings"""
    return ConfigurationManager(base_path)


def load_configuration_from_file(file_path: Optional[Path] = None) -> DDNSConfiguration:
    """Convenience function to load configuration from file"""
    manager = ConfigurationManager()
    return manager.load_configuration(file_path)


def create_sample_configuration(format_type: str = "yaml") -> str:
    """Generate sample configuration in specified format

    Args:
        format_type: Either 'yaml' or 'json'
    """
    config_data = {
        "cloudflare": [
            {
                "authentication": {"api_token": "${CF_DDNS_API_TOKEN}"},
                "zone_id": "your-zone-id-here",
                "ttl": 300,
                "subdomains": [
                    {"name": "@", "proxied": True},
                    {"name": "www", "proxied": True},
                    {"name": "api", "proxied": False, "ttl": 120},
                    {"name": "home", "proxied": False, "ttl": 1},
                ],
            }
        ],
        "ttl": 300,
    }

    if format_type.lower() == "json":
        return json.dumps(config_data, indent=2)
    else:
        # Default to YAML with comments
        return """# DDNS Configuration Example
# Environment variables can be used with ${VARIABLE_NAME} syntax

cloudflare:
- authentication:
    # Use either api_token OR (api_key + api_email)
    api_token: "${CF_DDNS_API_TOKEN}"
    # api_key: "${CF_DDNS_API_KEY}"
    # api_email: "${CF_DDNS_API_EMAIL}"

    zone_id: "your-zone-id-here"

    # Optional: Zone-level default TTL (overrides global TTL)
    ttl: 300

    subdomains:
    - name: "@"          # Root domain
        proxied: true    # Use Cloudflare proxy

    - name: "www"        # www subdomain
        proxied: true

    - name: "api"        # API subdomain
        proxied: false   # DNS-only (no proxy)
        ttl: 120         # Custom TTL for this subdomain

    - name: "home"       # Home subdomain
        proxied: false
        ttl: 1           # Auto TTL (follows Cloudflare settings)

# Optional: Global TTL (used when not specified at zone/subdomain level)
ttl: 300
"""


def validate_configuration_command() -> int:
    """Standalone command to validate configuration file"""
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    try:
        print("🔍 Validating DDNS configuration...")
        manager = create_configuration_manager()

        # Show supported formats
        formats = manager.get_supported_formats()
        format_list = [f"{ext} ({name})" for ext, name in formats.items()]
        print(f"📋 Supported formats: {', '.join(format_list)}")

        config = manager.load_configuration()

        print("✅ Configuration is valid!")
        print(f"📊 Found {len(config.cloudflare_zones)} zones:")

        for i, zone in enumerate(config.cloudflare_zones, 1):
            auth_type = (
                "🔑 API Token" if zone.authentication.api_token else "🗝️  API Key"
            )
            print(f"\n  {i}. 🌐 Zone: {zone.zone_id}")
            print(f"     🔐 Auth: {auth_type}")
            print(f"     📂 Subdomains: {len(zone.subdomains)}")

            if zone.ttl:
                print(f"     ⏰ Zone TTL: {zone.ttl}s")

            for sub in zone.subdomains:
                effective_ttl = zone.get_effective_ttl(sub)
                ttl_source = ""
                if sub.ttl:
                    ttl_source = " (subdomain TTL)"
                elif zone.ttl:
                    ttl_source = " (zone TTL)"
                else:
                    ttl_source = " (default TTL)"

                proxy_info = " 🛡️ Proxied" if sub.proxied else " 🌐 DNS-only"
                print(
                    f"       - 📝 {sub.name} → TTL: {effective_ttl}s{ttl_source}{proxy_info}"
                )

        if config.global_ttl:
            print(f"\n🌍 Global TTL: {config.global_ttl}s")

        print("\n✨ Configuration validation completed successfully!")
        return 0

    except ConfigurationError as e:
        print(f"❌ Configuration error: {e}")
        return 1
    except Exception as e:
        print(f"💥 Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    import sys

    # Allow running different commands
    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "--validate":
            sys.exit(validate_configuration_command())
        elif command == "--sample":
            format_type = "yaml"
            if len(sys.argv) > 2:
                format_type = sys.argv[2]

            print(f"📄 Sample {format_type.upper()} configuration file:")
            print("=" * 40)
            print(create_sample_configuration(format_type))
            sys.exit(0)
        elif command == "--help":
            print("🔧 Configuration Manager Commands:")
            print("  --validate           Validate current configuration")
            print("  --sample [yaml|json] Show sample configuration file")
            print("  --help               Show this help message")
            sys.exit(0)

    # Default: Show example usage
    print("🚀 Configuration Manager Examples:")
    print()

    # Show supported formats
    manager = create_configuration_manager()
    formats = manager.get_supported_formats()
    format_list = [f"{ext} ({name})" for ext, name in formats.items()]
    print(f"📋 Supported formats: {', '.join(format_list)}")
    print()

    # Example 1: Basic usage
    try:
        config = manager.load_configuration()

        print(f"✅ Loaded {len(config.cloudflare_zones)} zones")
        for zone in config.cloudflare_zones:
            print(f"   📂 Zone: {zone.zone_id} with {len(zone.subdomains)} subdomains")

    except ConfigurationError as e:
        print(f"❌ Configuration error: {e}")
        print("💡 Run with --sample to see example configuration")

    print()
    print("🔧 Builder Pattern Example:")

    # Example 2: Builder pattern
    builder_config = (
        ConfigurationBuilder()
        .set_global_ttl(300)
        .add_zone("zone123", api_token="token123")
        .add_subdomain("www", proxied=True)
        .add_subdomain("api", proxied=False, ttl=120)
        .done()
        .build()
    )

    print(f"   🏗️ Built config with {len(builder_config.cloudflare_zones)} zones")

    # Example 3: TTL calculation
    print("\n⏰ TTL Resolution Example:")
    for zone in builder_config.cloudflare_zones:
        for subdomain in zone.subdomains:
            effective_ttl = zone.get_effective_ttl(subdomain)
            print(f"   {subdomain.name}: {effective_ttl}s TTL")
