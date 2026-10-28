# T3 Usage Monitor reference

[Back to the install and use guide](../README.md)

## Manual install

Copy `custom_components/t3_code` to `/config/custom_components/t3_code` in your
Home Assistant configuration directory. Or generate `dist/t3_code.zip` with
`python scripts/package.py` and extract it into `/config/custom_components/`.
Check that `/config/custom_components/t3_code/manifest.json` exists, restart Home
Assistant, then add T3 Usage Monitor in Settings > Devices & services.

To add the repository manually in HACS, open HACS > Custom repositories, enter
`https://github.com/0libote/T3-Usage-Monitor`, select Integration, and download T3 Usage Monitor.
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

Version 0.2 adds a T3 Usage Monitor sidebar dashboard automatically. It uses built-in Home
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
open Settings > Devices & services > Entities, filter by T3 Usage Monitor, and enable the
sensor you want. These details are classified as diagnostic sensors.

When upgrading from 0.1, existing detailed sensors stay active so automations
and recorded history keep working. They move to Diagnostics and are hidden
from automatically generated dashboards. Their IDs, custom names, precision
settings, and user-disabled states are preserved. You can unhide them or disable
ones you no longer need in the entity settings. The upgrade adjusts visibility
once, so later visibility changes remain yours.

To update an existing installation, download the update in HACS and restart Home
Assistant, then open T3 Usage Monitor in the sidebar.

## Dashboard and notification examples

For a device named `Development`, an Entities card might look like this. Replace
the entity IDs with those in your Home Assistant instance.

```yaml
type: entities
title: T3 Usage Monitor
entities:
  - entity: sensor.development_tokens_today
  - entity: sensor.development_estimated_cost_today
  - entity: sensor.development_estimated_cost_this_month
  - entity: sensor.development_codex_5_hour_remaining
  - entity: sensor.development_codex_5_hour_reset
```

Enable the optional reset-timestamp sensor before using it in this card.

A low-quota notification can use a numeric-state trigger:

```yaml
alias: T3 Usage Monitor low quota
triggers:
  - trigger: numeric_state
    entity_id: sensor.development_codex_5_hour_remaining
    below: 20
actions:
  - action: persistent_notification.create
    data:
      title: T3 Usage Monitor quota
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
Providers with history this month get zero-valued daily sensors on days with no
usage. Providers absent from the whole response get no new usage sensors;
previously created ones become unavailable.

Usage and quotas use independent requests. During runtime polling, a failed or
malformed usage response makes totals unavailable while healthy quotas continue
updating. A malformed quota account leaves other healthy accounts available and
appears as a failed account with an invalid-response explanation. A quota failure
leaves healthy usage totals available. Authentication failures still trigger
Home Assistant's reauthentication flow. Initial pairing
requires a valid usage response before the session is saved. If a metrics probe
fails after pairing, retrying reuses the session only for the same server
identity; a changed identity at the same address aborts setup before sending it.

The limits status reports `partial` when some accounts are healthy and others
have failed or expired, `failed` when probes fail without a healthy account,
`stale` when readings have expired, and `unsupported` when all reported accounts
are unsupported. The dashboard names affected accounts even when they have no
quota windows. An empty provider list reports no subscription limits.

API-key and other unsupported accounts may report no quota windows. Missing
quotas stay unavailable, rather than showing 0% or 100%. Failed probes suppress
old readings, including when T3 reports an unavailability reason this version
does not recognize. Quotas expire locally at their reset timestamp or two hours
after the provider's `checkedAt` timestamp, whichever comes first, including between
hourly polls. Missing, invalid, or more than five minutes future-dated check
timestamps also make readings unavailable. Reset-credit counts expire after
two hours. Fresh provider readings restore availability; the integration does
not redeem credits.

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
bash scripts/ci.sh ha-current
bash scripts/ci.sh ha-min
```

The locked test environments cover HA 2026.3.1 (the HACS minimum) and
2026.10.2. Each command runs Ruff, formatting, the full test suite, and archive
validation. The minimum environment builds HA's pinned `lru-dict` dependency
from source on Python 3.14. Both environments skip the unused `mock-open` and
`pyric` dependencies. Run `bash scripts/ci.sh ha-current` to restore and validate
the default current environment after minimum-version testing. CI also runs hassfest.

The tests use Home Assistant with a local HTTP/WebSocket fixture. They cover
pairing, RPC framing, auth failures, reauthentication, duplicate prevention,
source failures, quota expiry without a poll, token arithmetic, midnight/month
and DST boundaries, dynamic sensors, outage recovery, diagnostics downloads,
archive installation layout, and unload. Synthetic sanitized usage fixtures
cover the consumed common fields of contracts 4–6 over both supported protocols;
they are not a substitute for live tests against each server version.

An earlier live test against T3 server
`0.1.1-nightly.20261016.3190` (protocol 2) returned 67 normalized sensors. The
temporary credential was revoked afterward. This does not validate the 0.2.1
changes against a live T3 server. Deployment on a real HAOS device remains
unverified; follow the [HAOS installation and upgrade check](haos-smoke-test.md)
before publishing.

## Troubleshooting diagnostics

In Settings > Devices & services, open the integration's menu and choose
Download diagnostics. The integration's diagnostic data includes server and
protocol versions, usage contract/source status, pricing status, quota failure
reasons, and check/expiry timestamps. Accounts are numbered. It omits tokens,
server addresses, server/account names, environment/account IDs, raw error
messages, and filesystem paths. Home Assistant adds its standard system
information to the downloaded file.

## Release process

1. Update the version in `pyproject.toml` and `manifest.json`, add a changelog
   entry, and run `uv lock`. If the minimum HA version changes, update both
   `hacs.json` and the `ha-min` dependency group.
2. Run both CI environments and the HAOS smoke test. Review the changes.
3. Create and push a matching `v`-prefixed tag, for example `v0.2.1`.
4. The tag workflow runs both environments and hassfest, verifies the version
   and tag, builds `dist/t3_code.zip`, and creates a draft GitHub release with
   that asset. Review the draft and installation results before publishing it.

`python scripts/package.py --tag v0.2.1` can validate and package locally without
creating a release. The archive places `t3_code/` at its root for extraction
into Home Assistant's `custom_components/` directory.

Protocol references: [T3 usage contract](https://github.com/pingdotgg/t3code/blob/main/packages/contracts/src/usage.ts),
[T3 auth contract](https://github.com/pingdotgg/t3code/blob/main/packages/contracts/src/auth.ts),
[T3 quota contract](https://github.com/pingdotgg/t3code/blob/main/packages/contracts/src/providerUsageLimits.ts),
[T3 usage documentation](https://github.com/pingdotgg/t3code/blob/main/docs/user/usage.md),
and [Home Assistant integration docs](https://developers.home-assistant.io/docs/creating_integration_manifest/).
The initial implementation was checked against upstream commit
`1451e7cb23034d29774338608368a15d8845df36`.
