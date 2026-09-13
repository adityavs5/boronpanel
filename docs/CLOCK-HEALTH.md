# Clock synchronization and 2FA health

Ubuntu installation configures chrony with an additional authenticated Cloudflare
NTS source while retaining configured Ubuntu/operator sources. The configuration
is parsed before service restart. A systemd drop-in restarts chrony after failure
with a five-second delay and a five-starts-per-five-minutes limit. This handles
service crashes; chrony handles source retries and continuous clock correction.
It does not forcibly step the live clock from a panel request.

The administrator health page displays clock synchronization state, source, offset
and estimated error bound. The probe reads chronyc CSV with a three-second timeout
and a 30-second monotonic cache. A running service alone is insufficient: local-only
reference IDs, invalid strata, stale/future measurements, loss of synchronization,
and excessive uncertainty cause unhealthy status. Malformed/unavailable probes
produce unknown status instead of a false healthy signal.

Error bound is abs(system offset) + root dispersion + root delay/2, following
[chrony's tracking documentation](https://chrony-project.org/doc/4.8/chronyc.html).
One second produces a warning; five seconds produces critical status. Measurements
must be recent relative to their update interval, capped at one hour. These are
Boron early-warning thresholds, not changes to TOTP's accepted code window.

The existing five-minute service monitor includes clock health and retains history,
alert cooldown and recovery transitions. Delivery uses the existing configured
monitoring recipient/sender and must be enabled to send alerts. Recovery codes remain
single-use and independent of the clock. The full admin/customer 2FA browser and live
login audit remains part of the overall goal.

Live evidence: chrony selected 162.159.200.1 after applying restart protection;
status returned healthy, offset 0.000000449 s, estimated error bound 0.0839566075 s.
The new probe reported unsynchronized immediately after restart and then recovered.
Systemd reported active, Restart=on-failure, RestartUSec=5s, StartLimitBurst=5.
These changes to chrony are live. New panel health UI/monitoring code is not deployed.

Validation: final 45 backend checks passed, including drift/staleness/malformed probe
cases, caching, monitoring failure/cooldown/recovery behavior and TOTP/single-use
recovery codes. Production build and all four theme/mode clock browser checks passed;
mobile rendering was visually reviewed. Logs are retained under
`/root/boron-setup/clock-health-`. A final wording correction points to Service
monitoring rather than assuming its location below the clock card.
