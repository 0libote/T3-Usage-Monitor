# T3 Code → Home Assistant

A custom Home Assistant integration that reads token usage, estimated cost, and
subscription limits from an **existing T3 Code server**. Install it in Home
Assistant on HAOS, Container, or another supported installation.

Version 0.1.0 is an initial implementation. It connects directly over T3's HTTP
and WebSocket APIs; no additional bridge, MQTT broker, T3 plugin, or server patch
is required. It does not install or run T3 Code on HAOS.

## What you get

- Today and month-to-date token totals, with input, output, cache read, cache
  write, and reasoning breakdowns.
- Today and month-to-date estimated USD cost and cache savings.
- The same usage and cost sensors per provider, when that provider has history
  in the period. Usage history covers the accounts T3 includes in its summary.
- Used and remaining quota percentages and reset timestamps for each window
  reported by each configured provider instance, including additional accounts.
- Available reset-credit counts when reported. Credits are displayed only; this
  integration does not redeem them.
- Usage completeness, limit-refresh status, and last-read diagnostic sensors.
- One device per server, multiple server connections, credential replacement,
  address changes, and automatic discovery of newly reported quota windows.

Sensors work with Home Assistant dashboards, history, statistics, and automations.
Token/cost counters use calendar days and months in Home Assistant's configured
time zone. Quota sensors retain each provider's own session/weekly/monthly window.
The default polling interval is five minutes; Options allows 300–3600 seconds.

## Install on HAOS

Requires Home Assistant **2026.3 or newer** and a reachable T3 Code server.

### From this checkout or the ZIP

1. Copy `custom_components/t3_code` into your Home Assistant configuration
   directory as `/config/custom_components/t3_code`. Alternatively, generate
   `dist/t3_code.zip` with `python scripts/package.py` and extract it into
   `/config/custom_components/`. The final path must include
   `/config/custom_components/t3_code/manifest.json`.
2. Restart Home Assistant.
3. Open **Settings → Devices & services → Add integration → T3 Code**.
4. Enter the connection details described below.

### HACS custom repository

The repository uses HACS's integration layout. Once this source has been pushed
to GitHub, add `https://github.com/0libote/T3-x-HA` under **HACS → Custom
repositories**, select **Integration**, and download **T3 Code**. Restart Home
Assistant, then add the integration in Devices & services. This is not a listing
in the default HACS catalog or the HAOS app/add-on store.

## Connect your server

The address must be reachable **from Home Assistant**, for example
`http://192.168.1.20:3773` or your HTTPS reverse-proxy address. `localhost` on
HAOS refers to HAOS, not the computer running T3. Use the T3 application's
address, not a Docker setup console's `/_setup/` address.

### Pairing link or token

Create a fresh pairing link from T3's server access settings, or run this **on
the server using the same T3 data directory as the running application**:

```sh
t3 auth pairing create \
  --base-url http://192.168.1.20:3773 \
  --scope orchestration:read \
  --scope diagnostics:read \
  --label "Home Assistant" \
  --ttl 10m
```

Choose **Pairing token / link** in Home Assistant. Paste the whole
`http(s)://…/pair#token=…` link into the URL field, leaving the credential field
blank. Or enter the plain server URL and put the pairing token in the credential
field. The query-string form `/pair?token=…` is also accepted.

Pairing credentials are single-use. The integration exchanges the credential for
a bearer session requesting only `orchestration:read` and `diagnostics:read`.
Only the server origin and resulting session token are saved in the config entry.
The pairing-link lifetime controls when it may be redeemed, not the resulting
session lifetime, which T3 controls.

### Bearer token

For a headless connection, issue a scoped bearer token on the T3 server:

```sh
t3 auth session issue \
  --scope orchestration:read \
  --scope diagnostics:read \
  --label "Home Assistant" \
  --ttl 30d \
  --token-only
```

Choose **Bearer token**, enter the server URL, and paste the issued token into
the credential field. Keep the token private. Home Assistant stores it in its
config entry, so treat HA configuration and backups as containing credentials.
Use a trusted local network or HTTPS; TLS certificate verification is enabled.

When a token expires or is revoked, Home Assistant prompts for a replacement.
Use **Reconfigure** to change the server's address or credential. The server's
identity must remain the same; add another integration for another server.
To remove access, delete the integration and revoke its session in T3's access
settings. Removing the integration alone does not revoke the T3 credential.

**T3 Connect cloud sign-in/relay is not implemented in this version.** A cloud
account login, relay discovery code, or app landing-page URL cannot replace a
directly reachable server address and pairing/bearer credential. A direct
pairing link from your existing server works even if you also use T3 Connect
for other clients.

## Dashboard example

Add an Entities card and pick the sensors from the T3 Code device. For a device
named `Development`, an example is below; replace entity IDs with those shown in
your Home Assistant instance.

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

## Data behavior and current limits

- Costs are T3's **estimated API-equivalent costs**, not subscription invoices.
  Unpriced records may make costs incomplete; their count is a sensor attribute.
  Configure model prices in T3 to improve coverage. This integration never
  invents a price or derives token counts from conversation text.
- Total tokens add uncached input, cached input, cache writes, and output.
  Reasoning is already included in output and is not added a second time.
- History depends on what T3 can read. A partial or refreshing source marks the
  usage status `partial` and adds `incomplete: true` to usage sensors. If every
  source is missing or failed, totals are unavailable. An empty successful
  history is zero. Providers with no bucket in a period have no new per-provider
  sensors for that period; previously created ones become unavailable.
- API-key and other unsupported accounts may expose no quota windows. Missing
  limits are unavailable, never an assumed 0% or 100%. Probe failures suppress
  old readings; quota percentages whose reset time has passed are also
  unavailable until T3 reports a current window. Expiry handling does not
  guarantee freshness when a provider omits its reset timestamp; inspect the
  `checked_at` attribute when needed.
- Limits are reported per configured provider instance. This version does not
  pool/deduplicate accounts across servers, or collect CLIProxyAPI hub-only
  accounts. Adding the same provider account on two servers can expose it twice.
- Polling requests current-month daily history and provider status. It does not
  backfill old months into HA Recorder. Recorder statistics begin when the
  sensors are installed. Estimates can decrease after T3 reparses history or
  changes pricing; counters support these corrections and calendar resets.
- Only the existing server metrics API is used. No agent runs, terminal
  commands, provider resets, model rediscovery, or provider settings writes are
  requested by the integration. T3's normal provider-status refresh may contact
  the providers to obtain current limits.
- Supported wire formats: orchestration protocols 1–2, usage contracts 4–6.
  Future incompatible contracts fail visibly instead of silently returning zero.

## Development and validation

```sh
uv sync --group dev
uv run ruff check custom_components tests scripts
uv run ruff format --check custom_components tests scripts
uv run pytest -q
uv run python scripts/package.py
```

Validation: **45 tests passing**, **93% integration coverage**, Ruff checks
passing, and **hassfest passing** against Home Assistant 2026.10.2.

Tests run Home Assistant with an actual local HTTP/WebSocket fixture and cover
pairing, RPC framing, auth failures, reauthentication, duplicate prevention,
source failures, quota freshness, token arithmetic, calendar resets, dynamic
sensors, and unload. The API was also smoke-tested against a running T3 server
`0.1.1-nightly.20261016.3190` (protocol 2), which returned 67 normalized sensors.
The temporary validation credential was revoked afterward. No real HAOS device
has been used for deployment testing yet.

Protocol references: [T3 usage contract](https://github.com/pingdotgg/t3code/blob/main/packages/contracts/src/usage.ts),
[T3 auth contract](https://github.com/pingdotgg/t3code/blob/main/packages/contracts/src/auth.ts),
[T3 quota contract](https://github.com/pingdotgg/t3code/blob/main/packages/contracts/src/providerUsageLimits.ts),
[T3 usage documentation](https://github.com/pingdotgg/t3code/blob/main/docs/user/usage.md),
and [Home Assistant integration docs](https://developers.home-assistant.io/docs/creating_integration_manifest/).
The initial implementation was checked against upstream commit
`1451e7cb23034d29774338608368a15d8845df36`.
