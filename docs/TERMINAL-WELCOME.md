# Admin terminal welcome

Branding includes an admin-only terminal welcome editor, literal preview, explicit
save and Reset to BORON. New admin web-terminal sessions display the default BORON
ASCII artwork or the configured text, then an interactive shell prompt. Empty text
disables the artwork. Customer web-terminal sessions retain normal SSH login behavior.

Admin web sessions request an SSH exec PTY running interactive Bash without profile
or rc files. This skips Ubuntu login MOTD/status output without changing SSH daemon
configuration, global MOTD files or customer shell configuration. Shell startup
customizations are intentionally not loaded for this admin web-terminal mode. The
SSH identity remains the selected hosting account, never a new root-shell grant.

Banner text is sent over the WebSocket as display text, never inserted in the shell
command. Only printable ASCII and newlines are accepted, limited to 30 lines/4000
characters; terminal control escapes are rejected. Private banner settings are read
through an admin-only API and excluded from public branding. An additive nullable
column preserves existing branding and uses BORON when unset.

Validation: production build and all four theme/mode browser checks passed. The
branding/terminal regression suite passed 54 tests. A real SSH session against the
dedicated wpdevqa account showed a prompt without Ubuntu MOTD and executed as the
account UID; the temporary session key was removed afterward. The proof script is
`/root/boron-setup/terminal-quiet-proof.py`; UI/test artifacts use the
`/root/boron-setup/terminal-welcome-` prefix. Panel deployment remains pending.

Final focused verification passed 10 tests, including idempotent migration preserving
an existing panel name, control-character rejection, literal shell-like text,
empty/reset behavior, public API privacy and customer denial of the admin endpoint.
