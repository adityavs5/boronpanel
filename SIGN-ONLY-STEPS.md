# Sign the tested Boron 3.2.0 assets without rebuilding

Owner-only instructions. Use the exact archive and metadata identified in the
external FINAL-GO-NO-GO.md and its tested-artifacts.json. Do NOT run release.sh,
npm, git archive or any rebuild. Signing writes detached .sig files; it does
not alter the tested tarball or metadata.

Any computer with Python 3.10+ and cryptography can sign and verify (Linux,
macOS or Windows). No root, running panel, GNU tar, Node or services are needed.
Use Python 3.12 as a tested runtime. Copy the reviewed source files
scripts/sign_release.py, shared/release_signature.py and
 deploy/release-ed25519-public.hex preserving their directory structure, or
use the reviewed release checkout. The private key stays on the owner's trusted
computer. The existing signer accepts an unencrypted Ed25519 PEM; an encrypted
PEM is not supported by this script. Do not print or upload the private key.

Create an isolated Python environment, install cryptography, and run from the
reviewed checkout. The following commands use a POSIX shell; on Windows use
.venv/Scripts/python.exe and equivalent environment-variable syntax.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install cryptography
export ASSET_DIR=/absolute/path/outside-the-checkout/to/tested-assets
# Place the independently reviewed tested-artifacts.json in this directory too.
.venv/bin/python - <<'PY'
import hashlib,json,os
from pathlib import Path
p=Path(os.environ['ASSET_DIR'])
manifest=json.loads((p/'tested-artifacts.json').read_text())
for name,expected in manifest['sha256'].items():
    actual=hashlib.sha256((p/name).read_bytes()).hexdigest()
    assert actual==expected, f'Tested bytes do not match: {name}'
a='boron-3.2.0.tar.gz'
line=manifest['sha256'][a]+'  '+a+'\n'
c=p/'boron-3.2.0.sha256'
if c.exists(): assert c.read_text()==line, 'Checksum file differs'
else: c.write_text(line,encoding='ascii')
print('Exact tested archive and metadata verified')
PY
read -r -s -p 'Protected publisher PEM path: ' KEY_PATH
printf '\n'
.venv/bin/python -m scripts.sign_release 3.2.0 "$ASSET_DIR/boron-3.2.0.tar.gz" "$KEY_PATH"
.venv/bin/python -m scripts.sign_release 3.2.0 "$ASSET_DIR/boron-3.2.0.metadata.json" "$KEY_PATH"
unset KEY_PATH
.venv/bin/python - <<'PY'
import hashlib,json,os
from pathlib import Path
from shared.release_signature import archive_digest,verify_signature
p=Path(os.environ['ASSET_DIR'])
m=json.loads((p/'tested-artifacts.json').read_text())
for name,digest in m['sha256'].items():
    path=p/name
    assert archive_digest(path).hex()==digest, 'Bytes changed after testing'
    verify_signature('3.2.0',archive_digest(path),Path(str(path)+'.sig').read_bytes())
print('Both signatures valid against deploy/release-ed25519-public.hex; tested bytes unchanged')
PY
```

Never alter the pinned public key to make verification pass. The signer also
self-verifies against that key before writing each signature.

## Publish the exact tested release (owner only)

Proceed only when FINAL-GO-NO-GO.md reports the full privileged suite and
Playwright gate passed for these exact bytes. No throwaway repository is needed.
From the reviewed checkout, require a clean tree and the manifest commit:

```bash
git switch release/3.2.0
.venv/bin/python - <<'PYVERIFY'
import json,os,subprocess
from pathlib import Path
m=json.loads((Path(os.environ['ASSET_DIR'])/'tested-artifacts.json').read_text())
assert subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()==m['commit']
assert not subprocess.check_output(['git','status','--porcelain'],text=True).strip()
PYVERIFY
git tag -a v3.2.0 -m 'Boron v3.2.0'
git push origin HEAD refs/tags/v3.2.0
gh release create v3.2.0 --repo adityavs5/boronpanel --latest \
 --title 'Boron v3.2.0' --notes-file CHANGELOG.md \
 "$ASSET_DIR/boron-3.2.0.tar.gz" "$ASSET_DIR/boron-3.2.0.sha256" \
 "$ASSET_DIR/boron-3.2.0.tar.gz.sig" "$ASSET_DIR/boron-3.2.0.metadata.json" \
 "$ASSET_DIR/boron-3.2.0.metadata.json.sig"
```

This is a NORMAL release, neither draft nor prerelease. It pushes the release
branch and tag, without merging master. The agent does not execute these commands.
Download all five assets into a fresh directory and rerun the digest/signature
verifier above using the independently reviewed manifest (copy the manifest into
that directory first):

```bash
VERIFY_DIR=$(mktemp -d)
gh release download v3.2.0 --repo adityavs5/boronpanel --dir "$VERIFY_DIR"
cp "$ASSET_DIR/tested-artifacts.json" "$VERIFY_DIR/"
export ASSET_DIR="$VERIFY_DIR"
# Rerun the Python digest/signature verifier above.
```

Signing creates detached signatures without changing archive or metadata bytes.
The published bytes equal the tested bytes only after these comparisons pass.
Do not rebuild at any point. Tell the agent the normal release is published;
no repo name, GitHub token or private key needs to be shared.

## Unmodified baseline acceptance after publication

On the disposable VM, install the published v3.1.3 archive UNMODIFIED, verify
its pinned signature, seed disposable data, leave update_github_repo at its
default adityavs5/boronpanel, and update through the administrator Updates page.
Verify health, seeded account/domain/database/file data, services and rollback.
No loopback source, test override, substituted key or patched baseline is allowed.
The fresh-install/test gate does not claim this later acceptance has passed.

## If baseline acceptance fails (owner only)

First preserve the evidence and tested/signed assets offline. Roll the VM back
using its Updates page and verify recovery before removing release assets needed
by an in-flight update. Then remove only this candidate release/tag:

```bash
gh release delete v3.2.0 --repo adityavs5/boronpanel --yes
git push origin :refs/tags/v3.2.0
git tag -d v3.2.0
gh release edit v3.1.3 --repo adityavs5/boronpanel --latest
```

These delete the GitHub release/assets and remote/local tag, not the release
branch or master. If publication stopped midway, run only commands for objects
that actually exist. Never force-move an existing unrelated tag. Removing the
release does not undo an already installed update; VM rollback is separate.
