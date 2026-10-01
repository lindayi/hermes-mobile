# Hermes Mobile

Mobile web interface for Hermes Agent. This repository is the canonical source for reviewed changes and releases.

The initial source migration is performed through a pull request. The bootstrap commit contains no application source or production state.

## Development contract

- One task branch and Git worktree per independent change.
- All application changes go through GitHub pull requests, tests, and recorded reviews.
- Review findings are addressed by follow-up commits and replies in GitHub review threads.
- Merge current, tested, reviewed revisions into `main`; deploy only a verified `origin/main` revision.
- Resolve parallel-PR conflicts by reconciling both intents and testing both behaviors, not by blindly choosing one side.
- After merge, remove the task worktree, merged branch, and disposable artifacts. Keep only bounded deployment rollback material and necessary runtime data outside Git.
- Never commit credentials, account data, databases, private operational records, generated test output, or virtual environments.
