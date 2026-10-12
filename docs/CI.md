# Continuous integration

`CI` runs on every push and pull request using unprivileged Ubuntu 24.04
hosts. It installs pinned Python and Node dependencies, runs the correctness
lint, checks source files against the audited secret baseline, builds the React
SPA, then starts an ephemeral database, real RPC transport and HTTPS API for
the Playwright login/navigation smoke test.

The 4,000+ backend tests are deterministically divided across eight isolated
runners by a stable digest of each pytest node id. Every node id belongs to
exactly one shard. Each test has a 180-second watchdog and each shard has a
30-minute session limit, so a blocked test is reported by name. Coverage data
from all eight shards is combined before the report and least-covered-module
summary are produced.

`Nightly privileged Ubuntu integration` targets a self-hosted runner labelled
`boron-privileged-ephemeral`. That runner must be a freshly provisioned Ubuntu
24.04 VM that is destroyed after the job. The workflow runs the real installer,
which changes packages, system users, firewall rules, services and quotas. No
matching runner is currently provisioned in this repository, so the workflow
definition is runnable by hand but scheduled runs remain queued until the
owner connects that disposable runner. It verifies core services, listeners,
the RPC socket, cgroups v2 and the backend suite. P5 will add the N-1 upgrade
and rollback scenario; P9 will add the three installer profiles.
