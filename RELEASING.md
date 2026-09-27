# Versioning and releases

All changes go through pull requests targeting `main`. GitHub Actions runs the
offline checks on each pull request and creates a prerelease after a successful
merge when the manifest version has not been released before.

## Before merging

1. Create a topic branch from current `main` and open a pull request targeting
   `main`. Keep the pull request focused and summarize the user-visible change.
2. For a release-worthy component change, increment
   `custom_components/zentraly/manifest.json` if the current version already
   has a release, and add the change to `CHANGELOG.md`. Pull requests may share
   a version until the first merge publishes its prerelease. After that release
   exists, bump the version for the next release-worthy component change.
   Documentation-only and CI-only changes do not need a version bump.
3. Run the same offline checks used by GitHub Actions:

   ```powershell
   python -m pip install -r requirements-test.txt
   python -m pytest -q tests
   python -m ruff check custom_components/zentraly tests tests_upstream
   python -m compileall -q custom_components tests tests_upstream
   ```

4. Review the diff for credentials and private device data. Merge only after
   the required pull request checks pass.

## After merging

The `release` job in `.github/workflows/test.yml` runs only after the `tests`
job passes on a push to `main`. It reads the version from `manifest.json` and
creates a GitHub prerelease named `vX.Y.Z` at the exact merged commit. It skips
a version that already has a GitHub release, so later documentation or CI
merges do not create duplicate releases. A tag that points to a different
commit causes the job to fail. HACS uses the GitHub release source for that
version; no custom ZIP asset is needed.

To install a prerelease in HACS, enable prereleases for this repository and
select the exact candidate. Before testing, back up the installed component and
compare its files and manifest version with the release. Do not treat passing
CI as live device acceptance.

For a component change that affects device behavior or controls, verify the
affected device family before promoting the candidate to stable. Use the
family's documented read path and confirm writes from device readback: type 2
uses its documented `getConfig` contract; ZTTIN01 type 16 and boiler extension
type 17 use the documented `readAttr` contract. Keep live evidence separate
from offline tests. If a device family is unavailable for testing, record that
limitation rather than claiming live acceptance.

After the applicable acceptance checks pass, promote the same tag to stable;
do not move or recreate it:

```powershell
gh release edit vX.Y.Z --prerelease=false --latest
```

Record the Home Assistant version, tested device family, result and remaining
limitations in the test matrix. Never publish credentials, session exports,
backups or live logs with a release.
