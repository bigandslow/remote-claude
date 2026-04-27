#!/usr/bin/env python3
"""
Unit tests for docker_manager helpers.

These guard against regressions where bind-mount destinations or
session-storage paths drift from what claude inside the container
actually expects, which silently loses session transcripts.

Run with: python3 -m pytest tests/test_docker_manager.py -v
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.docker_manager import (  # noqa: E402
    _claude_projects_encoded_name,
    _get_worktree_info,
)


class TestClaudeProjectsEncodedName:
    """The directory name under ~/.claude/projects/ where claude writes
    its session transcript depends on claude's cwd inside the container.
    The bind-mount destination MUST match that name; otherwise transcripts
    are silently lost when the container exits.
    """

    def test_non_worktree_uses_workspace_alias(self):
        """Non-worktree containers run claude with cwd=/workspace,
        which encodes to '-workspace'."""
        path = Path("/Users/me/projects/myapp")
        assert _claude_projects_encoded_name(path, is_worktree=False) == "-workspace"

    def test_worktree_uses_encoded_host_path(self):
        """Worktree containers mount the workspace at its host path
        and run claude there, so the encoding matches the host path."""
        path = Path("/Users/me/.cache/cproj-workspaces/foo/bar_20260427_143522")
        expected = "-Users-me--cache-cproj-workspaces-foo-bar-20260427-143522"
        assert _claude_projects_encoded_name(path, is_worktree=True) == expected

    @pytest.mark.parametrize("char,replacement", [("/", "-"), (".", "-"), ("_", "-")])
    def test_encoding_replaces_slashes_dots_underscores(self, char, replacement):
        """Claude's encoding scheme replaces /, ., and _ with -."""
        path = Path(f"/a{char}b")
        encoded = _claude_projects_encoded_name(path, is_worktree=True)
        # Every separator should be replaced
        assert char not in encoded
        # And there should be a dash where the separator was
        assert replacement in encoded


class TestProjectsBindMount:
    """End-to-end check that the bind-mount destination string built into
    docker run args matches what claude inside the container will look for.
    This prevents silent transcript loss like the one we hit when worktree
    cwd encoding diverged from the hardcoded '-workspace' destination.
    """

    def test_non_worktree_mount_destination_is_workspace_alias(self):
        from lib.docker_manager import _projects_bind_mount

        host_workspace = Path("/Users/me/projects/myapp")
        host_projects_root = Path("/Users/me/.claude/projects")
        source, dest = _projects_bind_mount(
            workspace_path=host_workspace,
            host_projects_root=host_projects_root,
            is_worktree=False,
        )
        assert dest == "/home/claude/.claude/projects/-workspace"
        # Source uses the encoded host path so external `claude -c` finds it
        assert str(source) == str(
            host_projects_root / "-Users-me-projects-myapp"
        )

    def test_worktree_mount_destination_matches_host_path_encoding(self):
        """Regression guard for the bug where worktree containers wrote
        session transcripts to a path the bind mount didn't capture,
        losing them on container exit."""
        from lib.docker_manager import _projects_bind_mount

        host_workspace = Path(
            "/Users/me/.cache/cproj-workspaces/foo/bar_20260427_143522"
        )
        host_projects_root = Path("/Users/me/.claude/projects")
        source, dest = _projects_bind_mount(
            workspace_path=host_workspace,
            host_projects_root=host_projects_root,
            is_worktree=True,
        )
        # Both source and destination must use the same encoded host path
        # so claude's writes inside the container land on the host disk.
        encoded = "-Users-me--cache-cproj-workspaces-foo-bar-20260427-143522"
        assert dest == f"/home/claude/.claude/projects/{encoded}"
        assert str(source) == str(host_projects_root / encoded)


class TestStartContainerArgs:
    """Drive the full start_container code path (without invoking docker) and
    assert the assembled `docker run` args contain the correct projects bind
    mount. This catches regressions where the helpers exist but aren't used,
    or where the call site drifts from the helper contract.
    """

    def _build_manager(self, monkeypatch, claude_home: Path):
        """Construct a DockerManager wired to a temp claude_dir and stubbed
        subprocess so no real docker calls happen."""
        from lib import docker_manager as dm
        from lib.config import CredentialsConfig

        creds = CredentialsConfig(
            anthropic=Path("/nonexistent/anthropic"),
            claude=claude_home,
            git=Path("/nonexistent/.gitconfig"),
            ssh=Path("/nonexistent/.ssh"),
        )

        class FakeAccounts:
            default = "default"

        class FakeConfig:
            accounts = FakeAccounts()
            cloud = type("C", (), {"enabled": False})()
            tmux = type("T", (), {"socket_name": "rc", "session_prefix": "rc-"})()
            docker = type(
                "D", (), {"image": "remote-claude:configured", "network_mode": "default", "use_isolated_network": False}
            )()
            network = type("N", (), {"mode": "host", "use_isolated_network": False})()

            def get_credentials_for_account(self, _name):
                return creds

        manager = dm.DockerManager(FakeConfig())

        captured = {}

        def fake_run_docker(args, check=False, capture=False):
            # Skip the actual docker invocation; capture args for inspection.
            captured["args"] = list(args)

            class R:
                returncode = 0
                stdout = "deadbeef\n"
                stderr = ""

            return R()

        monkeypatch.setattr(manager, "_run_docker", fake_run_docker)
        # Avoid touching real files for hooks/plugins
        monkeypatch.setattr(dm, "_TEMP_FILES_TO_CLEANUP", set())
        return manager, captured

    def test_non_worktree_mount_in_run_args(self, monkeypatch, tmp_path):
        """Regression guard: a non-worktree workspace must produce a
        `-v <host>:/home/claude/.claude/projects/-workspace` argument."""
        claude_home = tmp_path / "claude"
        claude_home.mkdir()
        workspace = tmp_path / "myrepo"
        workspace.mkdir()  # not a git repo / worktree

        manager, captured = self._build_manager(monkeypatch, claude_home)
        manager.start_container(session_id="abc123", workspace_path=workspace)

        args = captured["args"]
        # Find the projects bind mount
        projects_mounts = [
            a for i, a in enumerate(args)
            if i > 0 and args[i - 1] == "-v" and ".claude/projects/" in a
        ]
        assert len(projects_mounts) == 1, f"got: {projects_mounts!r}"
        mount = projects_mounts[0]
        assert mount.endswith(":/home/claude/.claude/projects/-workspace"), (
            f"non-worktree should mount to '-workspace', got: {mount}"
        )

    def test_worktree_mount_in_run_args(self, monkeypatch, tmp_path):
        """Regression guard for the silent-transcript-loss bug: a worktree
        workspace must produce a `-v <host>:/home/claude/.claude/projects/<encoded-host-path>`
        argument so that claude's transcript writes inside the container reach
        the host disk."""
        claude_home = tmp_path / "claude"
        claude_home.mkdir()

        # Build a real worktree so _get_worktree_info returns truthy
        repo = tmp_path / "main-repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        wt_meta = repo / ".git" / "worktrees" / "feature"
        wt_meta.mkdir(parents=True)
        workspace = tmp_path / "feature"
        workspace.mkdir()
        (workspace / ".git").write_text(f"gitdir: {wt_meta}\n")

        manager, captured = self._build_manager(monkeypatch, claude_home)
        manager.start_container(session_id="abc123", workspace_path=workspace)

        args = captured["args"]
        projects_mounts = [
            a for i, a in enumerate(args)
            if i > 0 and args[i - 1] == "-v" and ".claude/projects/" in a
        ]
        assert len(projects_mounts) == 1, f"got: {projects_mounts!r}"
        mount = projects_mounts[0]

        encoded_workspace = (
            str(workspace).replace("/", "-").replace(".", "-").replace("_", "-")
        )
        expected_dest = f"/home/claude/.claude/projects/{encoded_workspace}"
        assert mount.endswith(":" + expected_dest), (
            f"worktree mount destination should match encoded host path; got: {mount}"
        )


class TestWorktreeDetection:
    """Worktree detection drives the mount destination choice. If detection
    is wrong, the wrong destination gets used and transcripts go missing."""

    def test_non_git_workspace_is_not_a_worktree(self, tmp_path):
        assert _get_worktree_info(tmp_path) is None

    def test_regular_git_repo_is_not_a_worktree(self, tmp_path):
        # A regular git repo has .git as a directory, not a file
        (tmp_path / ".git").mkdir()
        assert _get_worktree_info(tmp_path) is None

    def test_worktree_with_gitdir_pointer_is_detected(self, tmp_path):
        # Worktrees have .git as a file containing 'gitdir: <path>'
        repo = tmp_path / "main-repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        worktrees = repo / ".git" / "worktrees"
        worktrees.mkdir()
        wt = worktrees / "feature-branch"
        wt.mkdir()

        workspace = tmp_path / "feature-branch"
        workspace.mkdir()
        (workspace / ".git").write_text(f"gitdir: {wt}\n")

        result = _get_worktree_info(workspace)
        assert result is not None
        gitdir, name = result
        assert name == "feature-branch"
        assert gitdir == repo / ".git"
