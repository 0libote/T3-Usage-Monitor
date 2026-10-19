# T3 Code reference

[Back to the install and use guide](../README.md)

## Manual install

Copy `custom_components/t3_code` to `/config/custom_components/t3_code` in your
Home Assistant configuration directory. Or generate `dist/t3_code.zip` with
`python scripts/package.py` and extract it into `/config/custom_components/`.
Check that `/config/custom_components/t3_code/manifest.json` exists, restart Home
Assistant, then add T3 Code in Settings > Devices & services.

To add the repository manually in HACS, open HACS > Custom repositories, enter
`https://github.com/0libote/T3-x-HA`, select Integration, and download T3 Code.
Restart Home Assistant, then add the integration. This is a HACS custom
repository; it is not listed in the default HACS catalog or the HAOS app/add-on
store.

## Connection details

Use the T3 application's address or its HTTPS reverse-proxy address. A Docker
setup console's `/_setup/` address will not work. Home Assistant must be able to
reach the server directly, even if you use T3 Connect for other clients. Cloud
account logins, relay discovery codes, and app landing-page URLs are not
supported in this version.

### Pairing link or token

You can create a pairing link in T3's server access settings or run this on the
server, using the same T3 data directory as the running application:

```sh
t3 auth pairing create \
  --base-url http://192.168.1.20:3773 \
  --scope orchestration:read \
  --scope diagnostics:read \
  --label "Home Assistant" \
  --ttl 10m
```

Choose Pairing token / link in Home Assistant. Paste the full
`http(s)://…/pair#token=…` link into the URL field and leave the credential field
blank. Or enter the server URL and put the pairing token in the credential
field. The query-string form `/pair?token=…` also works.

Pairing tokens are single-use. The integration exchanges one for a bearer
session with `orchestration:read` and `diagnostics:read` permissions, then saves
only the server origin and session token. The pairing-link lifetime controls
when you can redeem it; T3 sets the resulting session's lifetime separately.

### Bearer token

Issue a scoped bearer token on the T3 server:

```sh
t3 auth session issue \
  --scope orchestration:read \
  --scope diagnostics:read \
  --label "Home Assistant" \
  --ttl 30d \
  --token-only
```

Choose Bearer token in Home Assistant, enter the server URL, and paste the token
into the credential field. Keep it private: Home Assistant saves it in the
config entry, so configuration files and backups contain credentials. Use HTTPS
or a trusted local network. TLS certificate verification is enabled.

## Sensor details and upgrades

Version 0.2 adds a T3 Code sidebar dashboard automatically. It uses built-in Home
Assistant cards, with compact token counts, costs rounded to two decimals, and
quota gauges grouped by account. Each connected server gets its own dashboard.
You can hide it in the integration's Options. Your existing dashboards are left
alone; use your own dashboard if you want to change the layout.

On a new installation, the main sensors are today's and this month's total
tokens and estimated cost, plus remaining quota for each reported window.
Reset times are included on the quota sensors. Token totals also carry the
input, output, and cache breakdowns as attributes. Raw values, token units,
statistics, and automation thresholds keep their full precision; rounding and
K/M/B abbreviations apply to the dashboard display.

Separate input, output, cache, reasoning, per-provider cost, quota-used,
reset-timestamp, and reset-credit sensors are disabled by default. To use one,
open Settings > Devices & services > Entities, filter by T3 Code, and enable the
sensor you want. These details are classified as diagnostic sensors.

When upgrading from 0.1, existing detailed sensors stay active so automations
and recorded history keep working. They move to Diagnostics and are hidden
from automatically generated dashboards. Their IDs, custom names, precision
settings, and user-disabled states are preserved. You can unhide them or disable
ones you no longer need in the entity settings. The upgrade adjusts visibility
once, so later visibility changes remain yours.

To update an existing installation, download the update in HACS and restart Home
Assistant, then open T3 Code in the sidebar.

## Dashboard and notification examples

For a device named `Development`, an Entities card might look like this. Replace
the entity IDs with those in your Home Assistant instance.

```yaml
type: entities
title: T3 Code
entities:
  - entity: sensor.development_today_tokens
  - entity: sensor.development_today_estimated_cost
  - entity: sensor.development_month_estimated_cost
  - entity: sensor.development_codex_5_hour_remaining
  - entity: sensor.development_codex_5_hour_reset
```

A low-quota notification can use a numeric-state trigger:

```yaml
alias: T3 Code low quota
triggers:
  - trigger: numeric_state
    entity_id: sensor.development_codex_5_hour_remaining
    below: 20
actions:
  - action: persistent_notification.create
    data:
      title: T3 Code quota
      message: Codex has less than 20% remaining in its session window.
```

## Data and limits

Each server gets one device. You can connect several servers, and new quota
windows appear automatically. Usage and cost sensors cover all accounts in T3's
summary. Optional per-provider sensors are available when it has history in the period.
Quota sensors belong to each configured provider instance, including additional
accounts. Sensors support dashboards, history, statistics, and automations.

Token breakdowns include input, output, cache reads, cache writes, and reasoning.
Total tokens add uncached input, cached input, cache writes, and output.
Reasoning is already part of output and is counted once.

Cost sensors show daily and month-to-date estimates and cache savings. Unpriced
records can make costs incomplete; their count is a sensor attribute. Set model
prices in T3 to improve coverage. The integration does not invent prices or
estimate tokens from conversation text.

Diagnostic sensors report usage completeness, limit-refresh status, and the
last read time. A partial or refreshing source sets the usage status to
`partial` and adds `incomplete: true` to usage sensors. If every source is missing
or failed, totals are unavailable. An empty successful history is zero.
Providers with no bucket in a period get no new usage sensors for that period;
previously created ones become unavailable.

API-key and other unsupported accounts may report no quota windows. Missing
quotas stay unavailable, rather than showing 0% or 100%. Failed probes suppress
old readings. Percentages with a reset time in the past also stay unavailable
until T3 reports a current window. When a provider omits its reset timestamp,
check the `checked_at` attribute for freshness. Reset-credit counts are shown
when reported; the integration does not redeem credits.

Accounts are not pooled or deduplicated across servers, and CLIProxyAPI hub-only
accounts are not collected. Connecting the same provider account on two servers
can show it twice.

Polling fetches current-month daily history and provider status. Recorder
statistics start when you install the sensors; earlier months are not
backfilled. Estimates can decrease when T3 reparses history or changes pricing.
The counters support those corrections and calendar resets.

The integration uses T3's existing HTTP and WebSocket APIs. It needs no bridge,
MQTT broker, T3 plugin, or server patch, and it does not run T3 Code on HAOS.
It requests no agent runs, terminal commands, provider resets, model
rediscovery, or provider settings writes. T3's normal status refresh may contact
providers for current limits.

Supported formats are orchestration protocols 1 to 2 and usage contracts 4 to 6.
An incompatible contract produces an error instead of zero readings.

## Development and validation

```sh
uv sync --group dev
uv run ruff check custom_components tests scripts
uv run ruff format --check custom_components tests scripts
uv run pytest -q
uv run python scripts/package.py
```

Validation against Home Assistant 2026.10.2: 61 tests passing, 94% integration
coverage, and Ruff and hassfest passing.

The tests use Home Assistant with a local HTTP/WebSocket fixture. They cover
pairing, RPC framing, auth failures, reauthentication, duplicate prevention,
source failures, quota freshness, token arithmetic, calendar resets, dynamic
sensors, and unload. A live test against T3 server
`0.1.1-nightly.20261016.3190` (protocol 2) returned 67 normalized sensors. The
temporary credential was revoked afterward. Deployment on a real HAOS device
has not been tested yet.

Protocol references: [T3 usage contract](https://github.com/pingdotgg/t3code/blob/main/packages/contracts/src/usage.ts),
[T3 auth contract](https://github.com/pingdotgg/t3code/blob/main/packages/contracts/src/auth.ts),
[T3 quota contract](https://github.com/pingdotgg/t3code/blob/main/packages/contracts/src/providerUsageLimits.ts),
[T3 usage documentation](https://github.com/pingdotgg/t3code/blob/main/docs/user/usage.md),
and [Home Assistant integration docs](https://developers.home-assistant.io/docs/creating_integration_manifest/).
The initial implementation was checked against upstream commit
`1451e7cb23034d29774338608368a15d8845df36`.
