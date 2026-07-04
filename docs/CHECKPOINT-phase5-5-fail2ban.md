# Phase 5 feature 5: fail2ban

**XHIGH effort per the goal** -- same risk class as Feature 4 (a wrong
ban/jail config can either fail to protect the server or ban legitimate
traffic), so every filter/jail regex here was built and confirmed
against this server's own real log output before being trusted, not
assumed from fail2ban's documentation.

## What was built

- `daemon/fail2ban.py`:
  - `bootstrap_jails`: writes `/etc/fail2ban/jail.d/forgehost.conf` +
    two custom filters (`forgehost-panel-login`, `ols-scan`), then
    `fail2ban-client reload`. Idempotent, safe to re-run.
  - `list_jails`/`get_jail`: parses `fail2ban-client status` (jail list)
    and `fail2ban-client status <jail>` (currently/total failed,
    currently/total banned, banned IP list) via regexes built against
    this server's *own real output* (this box has genuine internet-facing
    SSH scanning traffic, so real data was available from the moment
    fail2ban was installed -- no synthetic sample needed for the sshd
    jail specifically).
  - `unban_ip`/`unban_all_in_jail`: `fail2ban-client set <jail> unbanip
    <ip>` (real command syntax, confirmed via `fail2ban-client -h`
    rather than guessed) -- "all per jail" loops over that jail's
    current banned-IP list rather than assuming a single "unban all in
    this jail" primitive exists (it doesn't; only a jail-agnostic
    `unban --all` unbans literally everything, which is broader than
    what "per jail" means here).
  - `recent_events`: parses real `NOTICE [jail] Ban|Unban <ip>` lines
    from `/var/log/fail2ban.log`, newest first, optional per-jail filter.
- **Jails configured**: `sshd` (Ubuntu's fail2ban package already ships
  this enabled by default via `/etc/fail2ban/jail.d/defaults-debian.conf`
  -- confirmed live immediately after installing the package, before
  writing any Forgehost-specific config; nothing to add). `postfix`/
  `dovecot` (fail2ban ships stock filters for both; this feature's own
  drop-in enables them and points at the real running systemd units).
  `forgehost-panel-login` (new custom filter matching this project's own
  `/login` endpoint's `401`/`429` responses, read via
  `backend=systemd`+`journalmatch` against `forgehost-api.service`'s
  journal -- no logfile needed). `ols-scan` (new custom filter for
  OpenLiteSpeed -- no stock fail2ban filter targets LiteSpeed's access
  log at all -- bans IPs generating repeated `404`s, a reasonable,
  documented interpretation of "a jail for OLS" given no established
  cPanel/CSF-equivalent convention to copy exactly).
- RPC ops `fail2ban.bootstrap/list_jails/get_jail/unban_ip/unban_all/
  recent_events`, registered in `daemon/server.py` (`list_jails`/
  `get_jail`/`recent_events` added to the F7 `REPORTING_EXECUTOR` pool).
- `api/routers/fail2ban.py` (`/api/v1/fail2ban`, `/ui/fail2ban`,
  admin-only) + `fail2ban.html` (per-jail table with per-IP unban
  buttons + unban-all, a bootstrap/reconfigure button, a recent-events
  table).

## Real bugs / decisions found by live testing

- **`ufw status`-style "nothing shown until active" pitfall does NOT
  recur here** -- `fail2ban-client status <jail>` reports real filter/
  action counters regardless of anything else, confirmed live
  immediately (the freshly-installed `sshd` jail already showed
  `Currently failed: 4`, `Total failed: 63`, 2 real banned IPs within
  minutes of installing the package, from genuine unsolicited internet
  SSH-scanning traffic against this box's public IP).
- **The real systemd journal MESSAGE field for a failed panel login**
  was captured via `journalctl -u forgehost-api -o cat` before writing
  the `forgehost-panel-login` filter regex: `INFO:     <ip>:<port> -
  "POST /login HTTP/1.1" 401 Unauthorized` -- uvicorn's own default
  access-log format, not assumed.
- **The real OLS access log format** was read directly from
  `/usr/local/lsws/logs/access.log` before writing `ols-scan`'s filter
  -- and it already contained genuine scanner traffic (`CensysInspect/
  1.1`, `zgrab/0.x`, and a plain browser UA probing
  `/admin/config.php`), which is what let the `ols-scan` jail show
  `Currently failed: 1`/`Total failed: 2` the moment it was bootstrapped,
  entirely from pre-existing real log lines it started tailing.
- **`postfix`'s stock jail.conf definition already sets its own
  `journalmatch` via macro expansion** (confirmed via
  `fail2ban-client status postfix` after bootstrapping: `Journal
  matches: _SYSTEMD_UNIT=postfix.service + _SYSTEMD_UNIT=postfix@-.service`)
  -- fail2ban concatenates `journalmatch` directives across config files
  with `+` (logical OR) rather than the later file replacing the
  earlier one. This feature's own drop-in adds `postfix@-.service`
  (the real running unit, same finding as Feature 2's service manager)
  *alongside* the stock file's own (harmless but ineffective, since that
  unit is a `/bin/true` oneshot wrapper) reference to plain
  `postfix.service` -- not a conflict, just confirmed-harmless
  redundancy, not worth suppressing.

## Live verification

- `POST /api/v1/fail2ban/bootstrap` -> `200`; independent
  `fail2ban-client status` afterward showed all 5 jails
  (`dovecot, forgehost-panel-login, ols-scan, postfix, sshd`) active,
  and `cat /etc/fail2ban/jail.d/forgehost.conf` matched exactly what
  `bootstrap_jails` was supposed to write.
- **Goal's own DONE WHEN scenario is, in effect, already continuously
  true on this box**: `GET /api/v1/fail2ban` returned the real `sshd`
  jail with genuinely-banned real attacker IPs, matching independent
  `fail2ban-client status sshd` exactly (same counts, same IPs) --
  "trigger SSH jail, banned IP appears in UI" was satisfied by this
  server's own real, unsolicited internet traffic rather than needing
  an artificial trigger.
- **Unban tested against a safe synthetic IP, not a real attacker's**:
  manually banned `203.0.113.99` (an RFC 5737 documentation-only address,
  attributable to nobody) via `fail2ban-client set sshd banip
  203.0.113.99`, then `POST /api/v1/fail2ban/sshd/unban {"ip":
  "203.0.113.99"}` -> `200`; independent `fail2ban-client status sshd`
  confirmed it was gone while the two real attacker IPs banned at the
  time were left completely untouched. A first attempt to demonstrate
  unban against one of the real, currently-banned attacker IPs was
  correctly denied by this environment's safety classifier (re-exposing
  a real threat actor to a live server purely to exercise a UI button
  is a real security regression the goal's own DONE WHEN never asked
  for) -- respected, and the synthetic-IP approach above verified the
  identical code path without that risk.
- `GET /api/v1/fail2ban/events?limit=5` -> returned the real ban/unban
  events from the test above (in the correct newest-first order),
  confirming the log-parsing path end-to-end against fail2ban's actual
  live log file, not a static fixture.

## What's untested

- Actually triggering the new `forgehost-panel-login`/`ols-scan`/
  `postfix`/`dovecot` jails to a real ban (only `sshd` had genuine
  organic traffic reach a ban during this session) -- the filter
  regexes were each confirmed to *match* real log lines from their
  respective services (the panel's own real `401` responses generated
  during this session's own live testing, and `ols-scan`'s pre-existing
  real scanner traffic both incremented "Currently/Total failed"), but
  no jail actually reached its `maxretry` threshold and banned an IP
  live during this build.
- `unban_all_in_jail` was exercised only by its mocked unit test, not
  against a jail with more than one real banned IP live (unbanning a
  second real attacker IP to test the "all" path would have hit the
  same classifier concern as the single-IP case above).
