# rpi-cloudflare-ddns

This Python script is designed to update your DNS records with your current external IP address. It uses the Cloudflare API to fetch and update the DNS records for a specified zone.

## Disclaimer

This script is currently configured to only update 'A' type DNS records for a single Cloudflare zone. The zone ID is hardcoded in an environment variable, as I only have access to one Cloudflare zone at the moment. If you need to update other record types or manage multiple zones, you will need to modify the script accordingly.

## Features

- Retrieves the external IP address of the machine using the `ipify.org` API.
- Fetches the existing DNS records for the specified Cloudflare zone.
- Updates the DNS records with the new external IP address.
- Logs all actions to a log file (`py_logs.log`) for debugging purposes.

## Prerequisites

- Python 3.7 or higher
- Cloudflare API credentials (API key and zone ID)
- Environment variables for Cloudflare credentials and zone ID

## Installation

1. Clone the repository:
```sh
git clone https://github.com/your-username/rpi-cloudflare-ddns.git
```
2. Change to the project directory:
```sh
cd rpi-cloudflare-ddns
```
3. Create a virtual environment and activate it:
```sh
python3 -m venv venv
source venv/bin/activate
```
4. Install the required dependencies:
```sh
pip install -r requirements.txt
```
5. Set the environment variables for Cloudflare credentials and zone ID:
```sh
export CLOUDFLARE_API_TOKEN=your_cloudflare_api_token
export ZONE_ID=your_cloudflare_zone_id
```
Note: If you don't have a token yet, follow the guide [Create API token](https://developers.cloudflare.com/fundamentals/api/get-started/create-token/)

6. Run the script:
```sh
python main.py
```

## Usage

The script will automatically update the DNS records with the current external IP address. You can set up a cron job or a systemd service to run the script periodically to keep the DNS records up-to-date.

## License

This project is licensed under the [MIT License](LICENSE).

## Acknowledgments

- The Cloudflare Python API library: https://github.com/cloudflare/python-cloudflare
- The `ipify.org` API for retrieving the external IP address

