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


class TestSetupTokenReading:
    """Reading ~/.claude/.setup-token must produce a clean single-line token,
    even if the file has stray whitespace or a wrapped paste with embedded
    newlines. Otherwise docker run gets `-e CLAUDE_CODE_OAUTH_TOKEN=multi\nline`,
    which either fails the run or sends a malformed token to claude — making
    new sessions silently prompt for login.
    """

    def test_reads_clean_token_unchanged(self, tmp_path):
        from lib.docker_manager import _read_oauth_token

        token = "sk-ant-oat01-abc123-veryLongTokenString"
        f = tmp_path / ".setup-token"
        f.write_text(token + "\n")
        assert _read_oauth_token(f) == token

    def test_strips_internal_whitespace_from_wrapped_paste(self, tmp_path):
        """Pasted tokens often wrap at terminal width. The pieces become
        separate lines in the file but conceptually they're one token."""
        from lib.docker_manager import _read_oauth_token

        wrapped = "sk-ant-oat01-abc\n123-veryLongTokenString"
        expected = "sk-ant-oat01-abc123-veryLongTokenString"
        f = tmp_path / ".setup-token"
        f.write_text(wrapped + "\n")
        assert _read_oauth_token(f) == expected

    def test_strips_carriage_returns(self, tmp_path):
        """A file edited on Windows or pasted from a tool that uses CRLF."""
        from lib.docker_manager import _read_oauth_token

        f = tmp_path / ".setup-token"
        f.write_text("sk-ant-token\r\n")
        assert _read_oauth_token(f) == "sk-ant-token"

    def test_returns_none_for_missing_file(self, tmp_path):
        from lib.docker_manager import _read_oauth_token

        assert _read_oauth_token(tmp_path / "does-not-exist") is None

    def test_returns_none_for_empty_file(self, tmp_path):
        from lib.docker_manager import _read_oauth_token

        f = tmp_path / ".setup-token"
        f.write_text("\n   \n")  # only whitespace
        assert _read_oauth_token(f) is None


class TestClaudeJsonMount:
    """The host's ~/.claude.json should not be bind-mounted RW at the
    canonical container path. Doing so causes:
      - Stale data: atomic-rename writes on host change the inode; the
        container's bind mount keeps the old inode, leaving a stale snapshot.
      - Concurrent clobber: multiple sessions writing to the same host file
        race and produce malformed JSON, which makes claude fall back to
        the first-run flow (theme picker, onboarding).

    The fix: mount host file at a secondary path read-only and have the
    entrypoint copy it to the writable canonical path during startup.
    """

    def _build_args(self, monkeypatch, claude_home: Path):
        """Drive start_container() with a stub _run_docker and return args."""
        from lib import docker_manager as dm
        from lib.config import CredentialsConfig

        # Simulate a host with a populated ~/.claude.json
        (claude_home.parent / ".claude.json").write_text('{"foo":"bar"}')

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
                # Override the home-dir-derived path so the test harness
                # consults claude_home.parent / .claude.json rather than the
                # real user's file.
                creds.__dict__["_test_home"] = claude_home.parent
                return creds

        manager = dm.DockerManager(FakeConfig())
        run_args = []

        def stub_run(args, check=False, capture=False):
            # Capture only the run invocation (start_container also calls images, etc.)
            if args and args[0] == "run":
                run_args.append(list(args))
            return type("R", (), {"returncode": 0, "stdout": "remote-claude:configured\n", "stderr": ""})()

        monkeypatch.setattr(manager, "_run_docker", stub_run)
        # Patch Path.home so `Path.home() / ".claude.json"` resolves into tmp
        monkeypatch.setattr(Path, "home", staticmethod(lambda: claude_home.parent))

        workspace = claude_home.parent / "myrepo"
        workspace.mkdir(exist_ok=True)
        manager.start_container(session_id="abc123", workspace_path=workspace)
        assert run_args, "start_container did not invoke `docker run`"
        return run_args[0]

    def test_claude_json_not_mounted_rw_at_canonical_path(self, monkeypatch, tmp_path):
        """No -v argument should mount host .claude.json RW at the canonical
        container path, because RW exposes the file to clobber across
        concurrent containers and to inode-staleness from atomic renames."""
        claude_home = tmp_path / "claude"
        claude_home.mkdir()
        args = self._build_args(monkeypatch, claude_home)

        bad_mounts = [
            a for i, a in enumerate(args)
            if i > 0 and args[i - 1] == "-v"
            and a.endswith(":/home/claude/.claude.json")  # RW (no :ro suffix)
        ]
        assert not bad_mounts, (
            "host .claude.json must not be bind-mounted RW at the canonical "
            f"path; found: {bad_mounts!r}"
        )

    def test_claude_json_mounted_ro_at_host_path(self, monkeypatch, tmp_path):
        """Host .claude.json should be exposed to the container at a
        secondary path with :ro, so the entrypoint can copy it to a writable
        location without risking clobber on the host."""
        claude_home = tmp_path / "claude"
        claude_home.mkdir()
        args = self._build_args(monkeypatch, claude_home)

        ro_mounts = [
            a for i, a in enumerate(args)
            if i > 0 and args[i - 1] == "-v"
            and ".claude.json-host:ro" in a
        ]
        assert ro_mounts, (
            "host .claude.json must be bind-mounted read-only at "
            f"/home/claude/.claude.json-host; got mounts: {[a for i,a in enumerate(args) if i>0 and args[i-1]=='-v']!r}"
        )


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
