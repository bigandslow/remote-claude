#!/bin/bash
# Fix permissions on dirs that Docker auto-created as root when setting up
# bind mounts. Without this, claude can't write to parent dirs of mounts
# (breaking turbo/pnpm caches, /compact, etc.).
#
# Idempotent and safe to call multiple times.

set -e

# 1) /home/claude/.claude/projects parent of the projects/<encoded> bind mount.
#    Pre-created in Dockerfile as claude-owned, but defensively re-chown
#    in case it regressed.
if [ -d /home/claude/.claude/projects ] && \
   [ "$(stat -c %U /home/claude/.claude/projects 2>/dev/null)" != "claude" ]; then
    sudo /usr/bin/chown claude:claude /home/claude/.claude/projects 2>/dev/null || true
fi

# 1b) The bind mount targets under /home/claude/.claude/projects/ themselves.
#     Docker sometimes surfaces these as root-owned (timing/host-fs state),
#     and claude can't write its session transcript there — silently losing
#     the session. Chown each child entry that's root-owned.
if [ -d /home/claude/.claude/projects ]; then
    for child in /home/claude/.claude/projects/*; do
        [ -e "$child" ] || continue
        if [ "$(stat -c %U "$child" 2>/dev/null)" = "root" ]; then
            sudo /usr/bin/chown claude:claude "$child" 2>/dev/null || true
        fi
    done
fi

# 2) Worktree gitdir parent dirs. The .git/ bind mount creates parent dirs
#    along its path implicitly as root. Walk up from the gitdir parent and
#    chown each root-owned dir until we hit one we don't own (i.e. system).
if [ -n "$RC_WORKTREE_GITDIR" ]; then
    parent="$(dirname "$RC_WORKTREE_GITDIR")"
    while [ -n "$parent" ] && [ "$parent" != "/" ] && [ "$parent" != "." ]; do
        owner="$(stat -c %U "$parent" 2>/dev/null || echo)"
        if [ "$owner" = "root" ]; then
            sudo /usr/bin/chown claude:claude "$parent" 2>/dev/null || break
            parent="$(dirname "$parent")"
        else
            break
        fi
    done
fi
