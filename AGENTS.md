# Repository agent guidance

## GitHub CLI and pull requests

- Check the current branch, working tree, and `origin` URL before pushing or creating a PR.
- On this macOS host, `gh` uses a token in the keyring. A sandboxed `gh auth status` may report that the token is invalid because it cannot access the keyring. If that happens, retry `gh auth status` with `sandbox_permissions: "require_escalated"` before asking the user to sign in.
- When the user has authorized a GitHub PR, run `gh pr create` with the same elevated permission if keyring access is needed. Do not claim that authentication is broken based only on the sandboxed result.
- If an approval review rejects a push or another external action, follow its stated requirements. Do not use another route to bypass the rejection.
