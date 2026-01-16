"""Tmux session management for remote-claude."""

import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class TmuxSession:
    """Represents a tmux session."""

    name: str
    created: str
    attached: bool
    windows: int


class TmuxManager:
    """Manages tmux sessions for remote-claude containers."""

    def __init__(self, socket_name: str = "remote-claude", prefix: str = "rc"):
        self.socket_name = socket_name
        self.prefix = prefix

    def _run_tmux(
        self, args: list[str], check: bool = True, capture: bool = True
    ) -> subprocess.CompletedProcess:
        """Run a tmux command with the configured socket."""
        cmd = ["tmux", "-L", self.socket_name] + args
        return subprocess.run(
            cmd,
            check=check,
            capture_output=capture,
            text=True,
        )

    def session_exists(self, session_name: str) -> bool:
        """Check if a tmux session exists."""
        result = self._run_tmux(
            ["has-session", "-t", session_name], check=False, capture=True
        )
        return result.returncode == 0

    def create_session(
        self,
        session_name: str,
        command: Optional[str] = None,
        working_dir: Optional[str] = None,
    ) -> bool:
        """Create a new tmux session.

        Args:
            session_name: Name for the new session
            command: Optional command to run in the session
            working_dir: Optional working directory

        Returns:
            True if session was created successfully
        """
        if self.session_exists(session_name):
            return False

        args = ["new-session", "-d", "-s", session_name]

        if working_dir:
            args.extend(["-c", working_dir])

        if command:
            args.append(command)

        result = self._run_tmux(args, check=False)
        return result.returncode == 0

    def kill_session(self, session_name: str) -> bool:
        """Kill a tmux session.

        Args:
            session_name: Name of session to kill

        Returns:
            True if session was killed successfully
        """
        if not self.session_exists(session_name):
            return False

        result = self._run_tmux(["kill-session", "-t", session_name], check=False)
        return result.returncode == 0

    def attach_session(self, session_name: str) -> None:
        """Attach to an existing tmux session.

        This replaces the current process with tmux attach.
        """
        import os

        os.execvp(
            "tmux",
            ["tmux", "-L", self.socket_name, "attach-session", "-t", session_name],
        )

    def send_keys(self, session_name: str, keys: str, enter: bool = True) -> bool:
        """Send keys to a tmux session.

        Args:
            session_name: Target session
            keys: Keys to send
            enter: Whether to press Enter after

        Returns:
            True if keys were sent successfully
        """
        args = ["send-keys", "-t", session_name, keys]
        if enter:
            args.append("Enter")

        result = self._run_tmux(args, check=False)
        return result.returncode == 0

    def list_sessions(self) -> list[TmuxSession]:
        """List all tmux sessions with our prefix.

        Returns:
            List of TmuxSession objects
        """
        result = self._run_tmux(
            [
                "list-sessions",
                "-F",
                "#{session_name}|#{session_created}|#{session_attached}|#{session_windows}",
            ],
            check=False,
        )

        if result.returncode != 0:
            return []

        sessions = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("|")
            if len(parts) >= 4 and parts[0].startswith(f"{self.prefix}-"):
                sessions.append(
                    TmuxSession(
                        name=parts[0],
                        created=parts[1],
                        attached=parts[2] == "1",
                        windows=int(parts[3]),
                    )
                )

        return sessions

    def get_session_name(self, identifier: str) -> str:
        """Generate a full session name from an identifier.

        Args:
            identifier: Short identifier (e.g., project name or number)

        Returns:
            Full session name with prefix
        """
        return f"{self.prefix}-{identifier}"

    def capture_pane(self, session_name: str, lines: int = 100) -> Optional[str]:
        """Capture the last N lines from a session's pane.

        Args:
            session_name: Target session
            lines: Number of lines to capture

        Returns:
            Captured text or None if failed
        """
        result = self._run_tmux(
            ["capture-pane", "-t", session_name, "-p", "-S", f"-{lines}"],
            check=False,
        )

        if result.returncode != 0:
            return None

        return result.stdout
