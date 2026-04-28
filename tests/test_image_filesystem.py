#!/usr/bin/env python3
"""
Integration tests for the remote-claude container image filesystem.

These tests run real docker containers to validate filesystem invariants
that aren't easy to verify by reading the Dockerfile alone — for example,
that bind-mounted directories don't end up root-owned and break writes
by the claude user inside the container.

Skipped automatically when docker isn't running or the image isn't built.

Run with: python3 -m pytest tests/test_image_filesystem.py -v
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

IMAGE = "remote-claude:latest"


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    result = subprocess.run(
        ["docker", "info"], capture_output=True, timeout=10
    )
    return result.returncode == 0


def _image_exists(image: str) -> bool:
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True,
        timeout=10,
    )
    return result.returncode == 0


pytestmark = [
    pytest.mark.skipif(not _docker_available(), reason="docker not available"),
    pytest.mark.skipif(
        not _image_exists(IMAGE),
        reason=f"{IMAGE} not built — run 'rc build' first",
    ),
]


def _run(*args: str, check: bool = True, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args),
        capture_output=True,
        text=True,
        check=check,
        timeout=30,
        **kw,
    )


def test_claude_user_can_mkdir_under_projects_with_bind_mount():
    """Regression guard for /compact EACCES.

    When the projects/-workspace bind mount is set up, Docker creates
    /home/claude/.claude/projects implicitly as root if the dir doesn't
    pre-exist in the image. That root ownership prevents the claude user
    from creating sibling project dirs, which compaction needs.

    The Dockerfile must pre-create the dir owned by claude so the bind
    mount uses the existing dir and ownership is preserved.
    """
    with tempfile.TemporaryDirectory() as tmp:
        # Mimic production: bind a host dir into projects/-workspace
        host_workspace_projects = Path(tmp) / "host-projects"
        host_workspace_projects.mkdir()

        proc = _run(
            "docker", "run", "--rm",
            "-v",
            f"{host_workspace_projects}:/home/claude/.claude/projects/-workspace",
            "--entrypoint", "bash",
            IMAGE,
            "-c",
            # Verify ownership AND verify mkdir works as the default user
            "stat -c '%U' /home/claude/.claude/projects && "
            "mkdir /home/claude/.claude/projects/test-sibling && "
            "echo OK",
            check=False,
        )
        assert proc.returncode == 0, (
            "claude user cannot mkdir under /home/claude/.claude/projects "
            "after a bind mount is set up. This breaks /compact and any "
            "session write that needs to create a sibling project dir.\n"
            f"stdout: {proc.stdout!r}\nstderr: {proc.stderr!r}"
        )
        # The first line of stdout is the owner reported by stat
        owner = proc.stdout.splitlines()[0].strip()
        assert owner == "claude", (
            f"/home/claude/.claude/projects must be owned by claude, "
            f"not {owner!r} — Docker likely auto-created it as root because "
            f"it wasn't pre-created in the Dockerfile."
        )


def test_default_user_in_container_is_claude():
    """The container must run as the claude user, not root. If this
    regresses, several other invariants fall apart silently."""
    proc = _run(
        "docker", "run", "--rm",
        "--entrypoint", "id",
        IMAGE, "-un",
    )
    assert proc.stdout.strip() == "claude"


def test_claude_user_can_write_under_gitdir_parent_in_worktree_layout():
    """Regression guard for tools that traverse to the main repo path.

    For worktree containers we mount the workspace at its host path AND
    the git commondir (`.git/`) at its host path. Docker creates the
    *parent* of the gitdir mount implicitly as root because the path
    didn't pre-exist in the image. Tools like turbo, pnpm, etc. then
    try to write cache dirs at the main repo root and hit EACCES.

    The startup permission fix-up (fix-mount-perms.sh, called from the
    entrypoint) must chown those parents to claude.
    """
    with tempfile.TemporaryDirectory() as tmp:
        host_workspace = Path(tmp) / "feature-branch"
        host_workspace.mkdir()
        host_main = Path(tmp) / "main-repo"
        host_main.mkdir()
        host_gitdir = host_main / ".git"
        host_gitdir.mkdir()
        worktrees = host_gitdir / "worktrees"
        worktrees.mkdir()
        wt_meta = worktrees / "feature-branch"
        wt_meta.mkdir()
        (host_workspace / ".git").write_text(f"gitdir: {wt_meta}\n")

        # Reproduce the production mount layout for a worktree container.
        # Run fix-mount-perms.sh (what the entrypoint calls), then verify
        # that claude can mkdir under the gitdir parent.
        proc = _run(
            "docker", "run", "--rm",
            "-v", f"{host_workspace}:{host_workspace}",
            "-v", f"{host_gitdir}:{host_gitdir}",
            "-e", f"RC_WORKTREE_WORKSPACE={host_workspace}",
            "-e", f"RC_WORKTREE_GITDIR={host_gitdir}",
            "--entrypoint", "bash",
            IMAGE,
            "-c",
            f"/usr/local/bin/fix-mount-perms.sh && "
            f"mkdir {host_main}/.turbo && echo OK",
            check=False,
        )
        assert proc.returncode == 0, (
            "claude user cannot write under the gitdir parent dir, which is "
            "where monorepo tools like turbo place caches. Docker likely "
            "auto-created the parent as root and fix-mount-perms.sh didn't "
            "fix it up.\n"
            f"stdout: {proc.stdout!r}\nstderr: {proc.stderr!r}"
        )
