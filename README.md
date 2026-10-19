# T3 Usage Monitor

A Home Assistant integration that monitors token usage, estimated USD cost, and
subscription limits from an existing T3 Code server. See daily and monthly
totals, provider breakdowns, quota reset times, and cache savings.

[![Install T3 Usage Monitor through HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=0libote&repository=T3-x-HA&category=integration)

## Install

You need Home Assistant 2026.3 or newer, [HACS](https://hacs.xyz/), and an existing
T3 Code server that Home Assistant can reach. This works on HAOS, Container, and
other supported Home Assistant installations.

1. Click the button above and open the repository in your Home Assistant instance.
2. Select **Download** in HACS, then restart Home Assistant.
3. Go to **Settings > Devices & services > Add integration** and choose **T3 Usage Monitor**.

For installation without HACS, see the [manual install guide](docs/reference.md#manual-install).

## Connect your T3 server

1. Create a fresh pairing link in your T3 server's access settings.
2. In Home Assistant, choose **Pairing token / link** and paste the whole link
   into **T3 server URL or pairing link**. Leave the credential field blank.
3. Submit the form to connect.

You can also enter the server URL and a separate pairing token. For a bearer
token, choose **Bearer token** and enter it in the credential field.
[Token commands and connection details](docs/reference.md#connection-details) are
available if you need them.

Use an address Home Assistant can reach, such as `http://192.168.1.20:3773`.
On HAOS, `localhost` points to HAOS itself. T3 Connect cloud sign-in and relay
connections are not supported; use your server's direct address and credential.

## Use it

Open **T3 Usage Monitor** in the Home Assistant sidebar. The dashboard shows cost and
tokens for today and this month, with quota gauges and reset times grouped by
provider. Large token counts read as `54.8M tokens`, and costs use two decimals.
It appears automatically after setup or an integration update and restart.

The main device page keeps four usage totals and each account's remaining
quota. Detailed token breakdowns and per-provider costs are optional sensors.
See [sensor details and upgrades](docs/reference.md#sensor-details-and-upgrades)
to enable them. You can also use the sensors in your own cards and automations;
[YAML examples](docs/reference.md#dashboard-and-notification-examples) cover both.

Readings refresh every five minutes. Open the integration's **Options** to change
that to any interval from 300 to 3600 seconds. Daily and monthly usage follows
Home Assistant's time zone; quotas follow each provider's own windows.
You can also turn the sidebar dashboard off in Options.

Costs are API-equivalent estimates, not subscription bills. Missing or stale
quotas show as unavailable. See [data and limits](docs/reference.md#data-and-limits)
for details about incomplete history and unsupported accounts.

## Change or remove a connection

Home Assistant prompts for a replacement when a token expires or is revoked.
Use **Reconfigure** to change the address or credential for the same server.
Add another T3 Usage Monitor integration to connect a different server.

To remove access, delete the integration and revoke its session in T3's access
settings. Deleting the integration alone leaves that session active.

[Developer setup and validation](docs/reference.md#development-and-validation)
