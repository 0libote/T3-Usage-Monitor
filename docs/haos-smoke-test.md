# HAOS installation and upgrade check

Run this on a test HAOS instance before publishing a release. Automated tests
exercise Home Assistant Core, packaging, and reloads; they do not exercise the
Supervisor, HACS download flow, or a device restart.

Status for 0.2.1: **not run on HAOS**. This development environment has no HAOS
instance, Docker, or VM runtime attached.

Record the HA Core, Supervisor, HAOS, HACS, T3 server, and integration versions,
installation method, date, and result. Do not include credentials or pairing
links in the record.

## Fresh installation

1. Use HA Core 2026.3.1 or newer. Confirm HA can reach the T3 server directly.
2. Install through the HACS custom repository. For an unpublished candidate,
   build `dist/t3_code.zip`, extract it into `/config/custom_components/`, and
   confirm `/config/custom_components/t3_code/manifest.json` exists.
3. Restart Home Assistant and confirm its logs contain no T3 import/setup errors.
4. Add T3 Usage Monitor with a fresh scoped pairing link. Confirm the four usage
   totals, quota sensors, and sidebar dashboard appear. Compare totals and
   quotas with T3, accounting for HA's time zone.
5. Download diagnostics from the integration's menu. Confirm it includes server
   and protocol versions, source status, quota status, and check timestamps.
   Confirm it omits credentials, URL, account names, and local paths.
6. Set hourly polling. Observe a quota reset: the old quota must become
   unavailable at the reset, before the next poll. A fresh poll restores it.
7. Stop the T3 server and refresh the integration. Confirm readings become
   unavailable. Restart T3 and confirm the next successful refresh recovers.
8. Toggle the sidebar option off and on. Reload the integration, then restart
   HAOS. Confirm the saved session reconnects without pairing again and there
   is one dashboard and one set of entities.

## Upgrade from 0.2.0

1. Install 0.2.0 and pair once. Record a primary entity ID, rename an entity,
   enable a detailed sensor, and set a custom polling interval.
2. Install the candidate over the existing integration and restart Home Assistant.
3. Confirm version 0.2.1, the same entity IDs, custom name, enabled sensor,
   options, and existing Recorder history. Confirm no new pairing is required.
4. Repeat the dashboard, diagnostics, reset, and outage checks above.
5. Remove the integration. Confirm its dashboard and entities unload without
   errors. Revoke the saved session separately in T3's access settings.

Attach the completed results to the release review. The tag workflow creates a
draft release so this check can be recorded before publishing it.
