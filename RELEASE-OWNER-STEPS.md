# Boron 3.2.0 owner release steps

Use SIGN-ONLY-STEPS.md. No rebuilding: sign the exact tested archive and metadata
with python -m scripts.sign_release on the owner's trusted computer, and verify
against deploy/release-ed25519-public.hex. The agent never accesses the real key.

1. Require A–C to pass in FINAL-GO-NO-GO.md: final commit, archive SHA-256,
   complete privileged backend totals and Playwright result. Compare both asset
   hashes to the independently reviewed tested-artifacts.json.
2. Sign exactly those two files. Verify both signatures and unchanged hashes.
   Do not run release.sh or a rebuild-based dry run.
3. Tag that exact release/3.2.0 commit and publish all five assets to
   adityavs5/boronpanel as a NORMAL latest release (not draft or prerelease),
   following SIGN-ONLY-STEPS.md. Download and compare published bytes again.
4. Tell the agent publication is complete. No throwaway repo or new credentials
   are needed. The agent then installs published v3.1.3 UNMODIFIED on the
   disposable VM, seeds data, keeps the default update_github_repo, and updates
   through the admin page from the real repository. It verifies health, data,
   services and rollback. This post-signing gate is still pending until run.
5. If acceptance fails, preserve evidence, roll back the VM, then use the exact
   release/tag deletion commands in SIGN-ONLY-STEPS.md. Removing a GitHub
   release does not roll back installed software.

The actual base is origin/master (7791a9e), not origin/main. The release branch
is one squashed commit above it. The final manifest identifies that commit;
master merging is a separate owner decision. No agent push, tag, publication,
signing or real-key access is authorized.
