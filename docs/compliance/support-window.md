# Support and LTS window

Agentic Ledger is pre-1.0. Versions are `0.MINOR.PATCH`; a minor release
carries features, a patch release carries fixes.

## The window

- **The latest minor receives every fix**, shipped as patch releases.
- **The previous minor receives security fixes only, for 90 days** after
  the next minor ships. A security fix is one that closes a
  vulnerability in the ledger itself (its wall, its access control, its
  storage, its dashboard); bugs that are not vulnerabilities land on the
  latest minor only.
- No other line is maintained. There is no long-term support line before
  1.0.

Worked example: 0.15.0 ships. 0.14.x keeps receiving security fixes
until 90 days after the 0.15.0 release date; 0.13.x and earlier receive
nothing. When 0.16.0 ships, 0.15.x enters its 90-day tail.

## Deprecation notice

A setting, endpoint, header or payload field that is going away is
announced in the changelog at least one minor release before it is
removed, keeps working through that release with a note in the docs,
and its removal is listed under Changed in the release that removes
it. Removals never land in a patch release. The one exception is a
security fix that cannot keep the old behaviour, which says so in its
entry.

## How fixes ship

A fix is a tag. Tagging triggers the release pipeline
(`.github/workflows/release.yml`): the full test suite must pass, then
the wheel is published to PyPI through trusted publishing, the container
image is built for linux/amd64 and linux/arm64, signed with Sigstore
cosign, and published with an SBOM and provenance attestation, and a
GitHub release is cut with the changelog entry. There is no manual step
between the tag and the artifacts, so a security fix is available to
`pip install --upgrade agentic-ledger` and `docker pull` the same hour.

Every release is listed in [CHANGELOG.md](../../CHANGELOG.md) with its
date. Security fixes say so in their entry.

## Pinning

Pin to a minor and take patches: `agentic-ledger~=0.15.0` on PyPI, or the
`0.15` image tag family. Pinning to an exact patch is safe within the
window and leaves you one `--upgrade` away from a security fix.

Storage is forward-compatible within the window: every schema change is
additive and applied at startup, so an upgrade never needs a migration
step. `tests/test_upgrade_safety.py` holds that promise on both SQLite
and Postgres.

## Reporting

Vulnerabilities go to the private channel in
[SECURITY.md](../../SECURITY.md). A report against a version inside the
window gets a fix; a report against an older version gets the fix on the
supported lines and a note to upgrade.
