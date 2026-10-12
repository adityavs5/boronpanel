# Release script operating notes

No publishing or signing was performed during this review.

## Requirements and portability

`scripts/release.sh` needs Bash, Git, GNU tar (`--sort=name`), sha256sum,
sed, Python with the repository dependencies, Node/npm and an authenticated
GitHub CLI for publication. The frontend currently uses the Node 20 build
runtime and `npm ci`. Signing requires cryptography and the owner's protected
Ed25519 key. The key must match the pinned public key; both archive and metadata
signatures are self-verified before any Git publication.

The script itself has no root check and does not start panel services. Its
normal full test gate calls `scripts/test.sh full`, requiring `.venv`, the
bounded test workspace and sufficient free space. Privileged integrations need
an isolated Ubuntu 24.04 host with the relevant services, UIDs, sockets and
cgroups; a normal workstation cannot claim that coverage. The installer is
Ubuntu-specific, while packaging can run on Linux without root. macOS is not
supported out of the box: BSD tar lacks GNU tar options and sha256sum is usually
absent; privileged Linux tests cannot run there. Use an Ubuntu build VM rather
than treating a Mac packaging attempt as release validation.

## Pipeline and exact publication effects

`main`: parse, preflight, version preparation, tests, fresh frontend build,
tracked-file staging (`git archive HEAD` with version/static overlays), tarball,
checksum, metadata, signatures, self-verification, publication.

`publish` commits version.py/frontend package files only when the version was
not already prepared. It creates local annotated tag `vVERSION`, then executes
`git push origin HEAD vVERSION`. This pushes the CURRENT BRANCH and the tag to
origin. It does not merge into master. `--repo OWNER/REPO` controls `gh release
create`, not the Git push destination: origin and --repo must refer to the same
repository. A prepared release/3.2.0 stays on that branch. `--prerelease` marks
the GitHub release only; ordinary `/releases/latest` discovery excludes it.

`--dry-run` still signs and verifies. It is NOT an unsigned-build command.

## Reusing a completed test gate

`--tested-commit FULL_40_CHARACTER_HEAD_ID` is an explicit owner attestation,
not an independently verified CI certificate. It requires exact HEAD, a clean
tracked/untracked working tree, an explicit version already in HEAD, and cannot
be combined with either legacy skip switch. It is checked before tests and
again after the fresh frontend build. It only omits `scripts/test.sh full`.
Signing and both signature self-verifications remain mandatory. Do not pass a
commit that has not completed the intended test gate. Ignored environment files
and toolchain dependencies are not covered by the commit identity.

Example for an already-tested and prepared release:

```bash
scripts/release.sh --tested-commit "$(git rev-parse HEAD)" --prerelease \
  --repo adityavs5/boronpanel 3.2.0
```

## Failure and recovery

`cleanup_on_exit` removes its temporary staging directory. On a pre-commit
failure after version bump it restores the three version files from Git.
Artifact files remain in the output directory. It does not undo a successful
commit, local tag, remote branch/tag push, or GitHub release. A push can partly
succeed; a failed `gh release create` can leave the tag remotely present.
Inspect actual state before retrying:

```bash
git status --short
git show-ref --tags v3.2.0
git ls-remote origin refs/heads/release/3.2.0 refs/tags/v3.2.0
gh release view v3.2.0 --repo adityavs5/boronpanel
```

Owner-only cleanup, ONLY for a failed unpublished candidate after verifying
nobody consumed it: delete the partial GitHub release, then remote tag, then
local tag. These commands are documentation, not actions performed here:

```bash
gh release delete v3.2.0 --repo adityavs5/boronpanel --yes
git push origin :refs/tags/v3.2.0
git tag -d v3.2.0
```

Do not force-reset a shared remote branch. Keep its valid prepared commit, or
use an ordinary corrective commit after review. Once any release has been
consumed, use a new version instead of replacing signed assets under the same
tag. A retry with an existing tag will fail at tag creation; do not blindly
rerun the full script. Preserve validated assets and investigate the failed
step first.
