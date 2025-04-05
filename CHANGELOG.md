# Changelog

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
