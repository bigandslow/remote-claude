# Changelog

## Unreleased

### Breaking Changes

#### Worktree containers now mount at host paths

**What changed:** Git worktree workspaces and their git commondir are now mounted at their exact host paths inside the container, instead of being remapped to `/workspace` and `/commondir`.

Previously:
- Workspace mounted at `/workspace`
- Git commondir mounted at `/commondir/.git`
- Path rewriting in entrypoint rewrote `gitdir`/`commondir` pointer files

Now:
- Workspace mounted at its host path (e.g. `/Users/you/.cache/workspaces/my-branch`)
- Git commondir mounted at its host path (e.g. `/Users/you/GitHub/myrepo/.git`)
- No path translation — git pointer files resolve natively
- Claude's working directory is set to the host workspace path

**Why:** The old remapping approach required rewriting `gitdir` and `commondir` pointer files in the worktree state directory. Docker Desktop for Mac (VirtioFS) does not honor read-write submounts under a read-only parent bind mount, which meant git writes failed. More critically, the path rewrites leaked back to the host, corrupting the worktree metadata files on disk.

**Migration:** No action required for new sessions. If you have existing sessions with corrupted worktree metadata, restore the affected files:

```bash
# In .git/worktrees/<branch-name>/
echo "../.." > commondir
echo "/path/to/workspace/.git" > gitdir
```

**Impact:** Any tooling or scripts that assumed the workspace is available at `/workspace` inside worktree containers will need to be updated. Non-worktree sessions are unaffected — the workspace is still mounted at `/workspace`.

---

### Other Changes

- Python 3.13 added to container image via deadsnakes PPA
- `rc setup --headless` no longer times out when credentials are already mounted (skips onboarding prompts that don't appear)
- Git validation check added to container entrypoint — warns on startup if git is broken
- `rc build` and `rc setup` added to allowed tools in project settings (no approval prompt)
