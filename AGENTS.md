# AGENTS.md

Instructions for contributors and coding agents in this repository.

## Changes and pull requests

- Work from a topic branch based on `main`; never push feature commits directly to `main`.
- Before delivery, run the checks in [RELEASING.md](RELEASING.md), review `git status --short`, `git diff --check` and the complete scoped diff, and stage only intended files. Report failures accurately.
- When a feature is complete and ready to hand off, commit and push its topic branch to GitHub and open or update a pull request targeting `main`. Use a draft while it still needs work; otherwise make it ready for review.
- If it is unclear whether the user considers the feature ready for delivery, ask one concise finalization check before committing and publishing. Do not repeat that check when the user has already requested a PR or release.
- A request to release the current changes authorizes the end-to-end release path: run the final checks, commit and push the branch, open a PR to `main`, merge it after required checks pass, and verify that GitHub Actions created the release. Never bypass failing or required checks; report blockers instead.
- GitHub Actions runs the same checks on pull requests. The repository ruleset `Require pull requests and tests on main` enforces PRs and the `tests` check for `main`.

## Versioning and releases

- `custom_components/zentraly/manifest.json` is the source of the integration version. Release tags use `vX.Y.Z`.
- When a release-worthy component change is included and the current version already has a release, increment the manifest version and add an entry to `CHANGELOG.md`. Pull requests may share a version until the first merge publishes its prerelease; later release-worthy component changes need a version bump. Documentation-only and CI-only changes do not need a version bump.
- After tests pass on `main`, `.github/workflows/test.yml` creates a GitHub prerelease for a version that has no release yet. It tags the exact merged commit and skips versions already released.
- Do not create or move release tags manually. Follow [RELEASING.md](RELEASING.md) to verify the candidate and promote that same release to stable.

## Safety

- Never add account credentials, sessions, device identifiers, private backups, or live logs to the repository.
- CI uses synthetic data only. Do not send commands to physical devices from CI.
- Live device writes require explicit user authorization, a documented reversible value, and confirmation by device readback. Never retry a write automatically.
- Keep the Home Assistant domain, config-entry identity, device identifiers, and entity unique IDs stable unless a migration is explicitly designed.
