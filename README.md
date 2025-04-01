# Raspberry Pi Cloudflare DDNS Updater

A Python-based Dynamic DNS updater for Cloudflare that automatically updates DNS records when your public IP address changes. Designed to be lightweight and containerized.

## Disclaimer

This script is configured to update 'A' DNS records across multiple Cloudflare zones. While the application is designed to handle multiple domains and zones efficiently, please note:

1. The script currently supports only IPv4 (A records) updates
2. All DNS records must be pre-existing in Cloudflare (the script doesn't create new records)
3. The script assumes all configured domains are managed under the same Cloudflare account
4. API rate limits apply based on your Cloudflare plan
5. While the script is designed to be efficient, large numbers of DNS records may impact performance

If you need to update other record types (like CNAME, MX, etc.) or require additional functionality, you will need to modify the script accordingly.


## Features

- Retrieves the external IP address of the machine using the `ipify.org` API.
- Fetches the existing DNS records for the specified Cloudflare zone.
- Updates the DNS records with the new external IP address.
- Logs all actions to a log file (`py_logs.log`) for debugging purposes.

## Prerequisites

- Python 3.7 or higher
- Docker installed on your system
- Cloudflare API Token with DNS edit permissions
- Your domain(s) managed by Cloudflare

## Configuration

Create a `config.json` file with your Cloudflare configuration:

```json
{
    "ttl": 300,
    "cloudflare": [
        {
            "authentication": {
                "api_token": "your-cloudflare-api-token"
            },
            "zone_id": "your-zone-id",
            "subdomains": [
                {
                    "name": "@",
                    "proxied": true
                },
                {
                    "name": "www",
                    "proxied": true
                }
            ]
        }
    ]
}
```

### Configuration Parameters

- `ttl`: Time-to-live for DNS records in seconds (defailts to 300)
- `cloudflare`: Array of zone configurations
    - `authentication.api_token`: Your Cloudflare API token
    - `zone_id`: Your Cloudflare zone ID
    - `subdomains`: Array of subdomain configurations
        `name`: Subdomain name (use "@" or empty "" for root domain)
        `proxied`: Whether to proxy through Cloudflare (true/false). Note: if `true`, sets TTL to Auto (300)

## Docker Deployment

### Using Docker Compose (Recommended)

1. Create a `docker-compose.yml` file:

```yml
services:
  ddns-updater:
    build: .
    network_mode: "host"
    environment:
      PUID: 1000
      PGID: 1000
      CF_DDNS_API_TOKEN: ${CF_DDNS_API_TOKEN}
    restart: unless-stopped
```

2. Run the container:

```sh
docker-compose up -d
```

### Using Docker CLI

1. Build the image:

```sh
docker build -t rpi-cloudflare-ddns .
```

2. Run the container:

```sh
docker run -d \
  --name rpi-cloudflare-ddns \
  --restart unless-stopped \
  rpi-cloudflare-ddns
```

## Environment Variables
- Define environmental variables starts with `CF_DDNS_` and use it in config.json (Example: `CF_DDNS_API_TOKEN`)

## Monitoring

### Logs

View container logs:

```sh
docker logs rpi-cloudflare-ddns
# or follow the logs
docker logs -f rpi-cloudflare-ddns
```

## Troubleshooting

Common issues and solutions:

1. **DNS records not updating**
    - Verify your API token has the correct permissions
    - Check the container logs for error messages
    - Verify your zone ID is correct
2. **Container stops unexpectedly**
    - Check container logs for error messages
    - Verify your configuration file is valid JSON
    - Ensure the container has internet access

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the [MIT License](LICENSE).

## Acknowledgments

- The Cloudflare Python API library: https://github.com/cloudflare/python-cloudflare
- The `ipify.org` API for retrieving the external IP address

