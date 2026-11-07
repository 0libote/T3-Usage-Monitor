# Changelog

## Unreleased

- Fix `except A, B:` clauses that only parse on Python 3.14; parenthesized
  forms work on every supported interpreter.
- Skip single malformed usage buckets instead of failing the whole summary,
  and report empty usage/quota responses as partial/unsupported, never `ok`.
- Require whole token counts (integral floats accepted, fractions rejected),
  validate totals are finite, and round money sums.
- Clamp float dust in quota percentages, escape server IDs in sensor keys so
  `a/b` can no longer overwrite `a`'s `b/c` window, and prefer a reported
  reset time over the two-hour check cap so long windows stop flapping.
- Assume UTC for naive server timestamps so readings can still expire.
- Only accept pairing tokens from the URL fragment; query-string tokens are
  rejected, and cleartext hosts outside loopback log a warning.
- Treat the wider family of RPC authorization errors as auth failures so dead
  sessions prompt reauth, and keep reauth pinned to its stored address.
- Escape markdown link syntax in server-controlled dashboard labels, show a
  waiting view when data is not ready yet, and guard local expiry so one bad
  snapshot cannot stop the timer chain.
- Bound the usage fetch and the midnight re-fetch with the shared timeout and
  stop the re-fetch from stranding mismatched day/age stamps.
- Keep the raw pairing token out of flow state; only the bearer is retained
  for retries.

- Treat unfamiliar usage-source statuses as partial instead of rejecting the
  whole usage response or pairing attempt.
- Collapse whitespace in server-provided account and window labels so they
  cannot inject extra sections into the dashboard.
- Bound quota refresh and its fallback with one shared timeout so a hung
  server cannot stall a poll past the shortest interval.
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
