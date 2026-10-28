# Changelog

## Unreleased

- Expire quota account statuses between polls even when an account reports no
  quota windows or reset credits.
- Keep healthy quota accounts available when another account returns malformed
  data, and explain invalid quota responses on the dashboard.
- Suppress cached quotas when the server reports an unfamiliar unavailability
  reason, and reject malformed account/window containers.
- Abort setup retries when the server identity changes before reusing a paired
  session.
- Correct example sensor IDs and document zero daily usage for providers with
  monthly history.

## 0.2.1

- Fix dashboard imports on March Home Assistant releases. The tested minimum is
  now HA 2026.3.1.
- Expire quotas locally at their reset time or two hours after the last provider
  check, including readings without a reset timestamp.
- Report failed, partially failed, stale, and unsupported quota accounts on the
  dashboard, even when they have no windows.
- Fetch usage and quotas through independent connections so one failure does
  not suppress the other. Authentication failures still require reauthentication.
- Add diagnostics downloads that omit credentials, addresses, names, and paths.
- Test HA 2026.3.1 and 2026.10.2, supported protocol/contract combinations,
  calendar boundaries, local expiry, network recovery, and diagnostics privacy.
- Validate package versions, release tags, and the advertised HA minimum. Create
  a version-checked ZIP and draft release after the validation workflow passes.
