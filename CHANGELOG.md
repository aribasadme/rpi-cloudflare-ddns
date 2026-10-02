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
