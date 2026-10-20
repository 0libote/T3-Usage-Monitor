# Changelog

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
