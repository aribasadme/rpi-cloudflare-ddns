# Changelog

## [v2.4.0] - 2026-10-02

### ✨ Added
- IPv6 support: `AAAA` records can now be kept in sync with the host's public IPv6 address.
- New per-subdomain `type` config field to opt into record types (e.g. `type: ["A", "AAAA"]`). Defaults to `["A"]` when omitted, so existing configurations are unaffected.
- IPv6 public-IP detection with fallback across `api6.ipify.org` and `ipv6.icanhazip.com`.

### 🔧 Changed
- IP address validation now uses the standard library `ipaddress` module and is address-family aware.
- Renamed the tracked `config.yaml` to `config-example.yaml`; copy it to `config.yaml` and edit it with your own values. `config.yaml`/`config.yml` are now git-ignored to avoid committing secrets.

### 🛠️ Notes
- Records are still update-only — the `AAAA` record must already exist in Cloudflare.
- On hosts without working IPv6 connectivity, IPv6 updates are skipped with a warning while IPv4 updates continue normally.

## [v2.3.0] - 2026-10-01

### ✨ Added
- New `ip_provider` module with caching and multi-source fallback for public IP detection.
- Dedicated `configuration_manager` module supporting both YAML and JSON configuration.
- Batch record management to update DNS records across multiple zones.
- `ddns_updater` module with authentication and zone validation, replacing `main.py`.
- Comprehensive test suite covering the configuration, record management, IP provider, and updater modules.
- Pre-commit integration (ruff, pyright) and build-time version argument for the Docker image.

### 🔧 Changed
- Multi-stage Dockerfile built on `trixie-slim` images for smaller, faster builds.
- Updated type hints to built-in generics and simplified TTL validation.
- Refactored `.dockerignore` and bumped CI actions and dependencies.

### 🐛 Fixed
- More specific exception handling in the DNS updater and its tests.

## [v2.2.0] - 2025-06-11

### ✨ Added
- Per-subdomain TTL values, overriding the global TTL when set.
- Support for a TTL value of `1` to enable Cloudflare Auto TTL.

### 🔧 Changed
- Minimum allowed TTL lowered to 60 seconds.

## [v2.1.0] - 2025-06-03

### ✨ Added
- CI pipeline to run tests, with coverage reporting uploaded to Codecov.

### 🔧 Changed
- Migrated project and dependency management to `uv`.
- Restructured the project folder layout.
- Restricted Docker image publishing to full semver tags only.

## [v2.0.0] - 2025-04-05

### ✨ Added
- Support for multiple Cloudflare zones and domains using a single config.
- Docker integration for easier containerized deployment.
- Centralized `config.yaml` file to replace environment variable configuration.
- Use of `ipify.org` for reliable external IP detection.
- Basic test suite to validate configuration and API operations.

### 🔧 Changed
- Major refactor of the script for modularity and clarity.
- Logging improvements for better diagnostics and visibility.

### 🐛 Fixed
- DNS records not updating due to inconsistent IP detection.
- Error handling for missing or invalid Cloudflare zone and record data.

### ⚠️ Breaking Changes
- Replaced configuration file from `json` to  `yaml`.
- Users are recommended to use Docker deployment instead of crontab or manual execution.

### 🛠️ Migration Notes
- Copy the new `config.yaml` example from the updated [README](https://github.com/aribasadme/rpi-cloudflare-ddns/blob/main/README.md).
- If using Docker:
  ```bash
  docker build -t rpi-cloudflare-ddns .
  docker run -v /path/to/config.yaml:/app/config.yaml cloudflare-ddns
  ```

## [v1.0.0] - 2025-04-02

### ✨ Added
- Initial release: Cloudflare Dynamic DNS updater for Raspberry Pi.
- JSON configuration file for Cloudflare credentials and records.
- Docker image published to the registry with a retry mechanism to keep the container running.
