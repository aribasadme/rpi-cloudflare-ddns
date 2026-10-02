# Raspberry Pi Cloudflare DDNS Updater

[![Tests](https://github.com/aribasadme/rpi-cloudflare-ddns/actions/workflows/tests.yml/badge.svg)](https://github.com/aribasadme/rpi-cloudflare-ddns/actions/workflows/tests.yml)
[![codecov](https://codecov.io/gh/aribasadme/rpi-cloudflare-ddns/branch/main/graph/badge.svg)](https://codecov.io/gh/aribasadme/rpi-cloudflare-ddns)

A Python-based Dynamic DNS updater for Cloudflare that automatically updates DNS records when your public IP address changes. Designed to be lightweight and containerized.

## Disclaimer

This script is configured to update 'A' (IPv4) and 'AAAA' (IPv6) DNS records across multiple Cloudflare zones. While the application is designed to handle multiple domains and zones efficiently, please note:

1. The script supports IPv4 (A records) and IPv6 (AAAA records) updates; IPv6 is opt-in per subdomain via the `type` field
2. **For IPv6, the updater must run on the exact machine you want the AAAA record to point to.** IPv6 is not NAT-ed: each host has its own public address, so the detected IPv6 is the address of the machine making the request. (IPv4 is different — behind NAT every device on the LAN shares the router's public address, so the detected IPv4 is the same regardless of which host runs the updater.)
3. All DNS records must be pre-existing in Cloudflare (the script doesn't create new records)
4. The script assumes all configured domains are managed under the same Cloudflare account
5. API rate limits apply based on your Cloudflare plan
6. While the script is designed to be efficient, large numbers of DNS records may impact performance

If you need to update other record types (like CNAME, MX, etc.) or require additional functionality, you will need to modify the script accordingly.

## Features

- Retrieves the external IPv4 address with automatic fallback across multiple providers (`api.ipify.org`, `checkip.amazonaws.com`, `icanhazip.com`).
- Optional IPv6 (AAAA) support with its own provider fallback (`api6.ipify.org`, `ipv6.icanhazip.com`); on hosts without IPv6 connectivity, IPv6 updates are skipped gracefully while IPv4 continues.
- Automatic IP address monitoring and DNS record updates
- Support for multiple Cloudflare zones and domains

## Prerequisites

- Python 3.12 or higher
- Docker installed on your system
- Cloudflare API Token with DNS edit permissions
- Your domain(s) managed by Cloudflare

## Configuration

Copy the provided `config-example.yaml` to `config.yaml` (or `config.yml`) and edit it with your Cloudflare configuration:

```bash
cp config-example.yaml config.yaml
```

The resulting file looks like:

```yml
cloudflare:
  - authentication:
      api_token: "your-cloudflare-api-token"
    zone_id: "your-zone-id"
    subdomains:
      - name: "@"
        proxied: false
      - name: "foo"
        proxied: false
ttl: 300
```

### Configuration Parameters

- `ttl`: Global time-to-live for DNS records in seconds (defaults to 300). Set to 1 for Auto TTL.
- `cloudflare`: Array of zone configurations
  - `authentication.api_token`: Your Cloudflare API token
  - `zone_id`: Your Cloudflare zone ID
  - `subdomains`: Array of subdomain configurations
    - `name`: Subdomain name (use "@" or empty "" for root domain)
    - `proxied`: Whether to proxy through Cloudflare (true/false)
    - `ttl`: (Optional) Time-to-live in seconds for this specific subdomain. Set to 1 for Auto TTL. If not set, uses the global TTL value.
    - `type`: (Optional) List of DNS record types to keep in sync. Supported values are `"A"` (IPv4) and `"AAAA"` (IPv6). Defaults to `["A"]`. Add `"AAAA"` to also update the IPv6 record, e.g. `type: ["A", "AAAA"]`. The matching record must already exist in Cloudflare.

Example configuration with per-subdomain TTL:

```yml
cloudflare:
  - authentication:
      api_token: "your-cloudflare-api-token"
    zone_id: "your-zone-id"
    subdomains:
      - name: "@"
        proxied: false
        ttl: 1 # Auto TTL for root domain
      - name: "foo"
        proxied: false
        ttl: 120 # 2 minutes TTL for foo subdomain
      - name: "bar"
        proxied: false # Will use global TTL
        type: ["A", "AAAA"] # Keep both IPv4 and IPv6 records in sync (defaults to ["A"])
ttl: 300 # Global TTL, used when not specified in subdomain
```

## Docker Deployment

### Environment Variables

- You can define environmental variables that starts with `CF_DDNS_` and use it in config.yaml (Example: `CF_DDNS_API_TOKEN`).

```bash
export CF_DDNS_API_TOKEN=your_cloudflare_api_token
```

- You can also use a `.env` file to manage them:

```text
CF_DDNS_API_TOKEN=your_cloudflare_api_token
CF_DDNS_ZONE_ID=your_cloudflare_zone_id
CHECK_INTERVAL=900
```

- Then edit you `config.yaml` accordingly:

```yml
cloudflare:
  - authentication:
      api_token: "${CF_DDNS_API_TOKEN}"
    zone_id: "${CF_DDNS_ZONE_ID}"
```

### Using Docker Compose (Recommended)

1. Create a `docker-compose.yml` file:

```yml
services:
  ddns-updater:
    image: aribasadme/cloudflare-ddns:latest
    volumes:
      - ./config.yaml:/app/config.yaml:ro
    environment:
      CF_DDNS_API_TOKEN: ${CF_DDNS_API_TOKEN}
      CF_DDNS_ZONE_ID: ${CF_DDNS_ZONE_ID}
      CHECK_INTERVAL: 900 # (optional) seconds
    restart: unless-stopped
```

2. Run the container:

```sh
docker-compose up -d
```

### Using Docker CLI

Run the container:

```sh
docker run -d \
  --name cloudflare-ddns \
  --restart unless-stopped \
  -e CF_DDNS_API_TOKEN=your_api_token \
  -e CF_DDNS_ZONE_ID=your_zone_id \
  -e CHECK_INTERVAL=900 \       # (optional) seconds
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  aribasadme/cloudflare-ddns:latest
```

or if using a `.env` file:

```sh
docker run -d \
  --name cloudflare-ddns \
  --restart unless-stopped \
  --env-file .env \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  aribasadme/cloudflare-ddns:latest
```

## Monitoring

### Logs

View container logs:

```sh
docker logs cloudflare-ddns
# or follow the logs
docker logs -f cloudflare-ddns
```

## Troubleshooting

Common issues and solutions:

1. **DNS records not updating**
   - Verify your API token has the correct permissions
   - Check the container logs for error messages
   - Verify your zone ID is correct
2. **Container stops unexpectedly**
   - Check container logs for error messages
   - Verify your configuration file is valid YAML (or JSON)
   - Run `python src/ddns_updater.py --validate` to check the configuration without starting the updater
   - Ensure the container has internet access

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the [MIT License](LICENSE).

## Acknowledgments

- The Cloudflare Python API library: https://github.com/cloudflare/python-cloudflare
- The `ipify.org`, `checkip.amazonaws.com`, and `icanhazip.com` services for retrieving the external IPv4 address, and `api6.ipify.org` / `ipv6.icanhazip.com` for IPv6
