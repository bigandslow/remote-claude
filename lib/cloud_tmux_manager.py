"""Remote tmux session management over SSH for cloud sessions.

CloudTmuxManager subclasses TmuxManager, overriding _run_tmux() to
execute all tmux commands on a remote cloud node via Tailscale SSH.
"""

import os
import subprocess
from typing import Optional

from .cloud_manager import SSH_OPTS, ssh_host
from .config import CloudNodeConfig
from .tmux_manager import TmuxManager


class CloudTmuxManager(TmuxManager):
    """Tmux operations on a remote cloud node via SSH."""

    def __init__(
        self,
        node: CloudNodeConfig,
        socket_name: str = "remote-claude",
        prefix: str = "rc",
    ):
        super().__init__(socket_name, prefix)
        self.node = node
        self._ssh_host = ssh_host(node)

    def _run_tmux(
        self, args: list[str], check: bool = True, capture: bool = True
    ) -> subprocess.CompletedProcess:
        """Run a tmux command on the remote node via Tailscale SSH."""
        cmd = [
            "ssh", *SSH_OPTS,
            self._ssh_host,
            "tmux", "-L", self.socket_name,
        ] + args
        return subprocess.run(
            cmd,
            check=check,
            capture_output=capture,
            text=True,
        )

    def attach_session(self, session_name: str, cc_mode: bool = False) -> None:
        """Attach to a remote tmux session via SSH.

        Replaces the current process with an SSH connection that
        attaches to the tmux session on the cloud node.

        Args:
            session_name: Name of the tmux session
            cc_mode: If True, use -CC (control mode) for iTerm2 native integration
        """
        tmux_cmd = ["tmux", "-L", self.socket_name]
        if cc_mode:
            tmux_cmd.append("-CC")
        tmux_cmd.extend(["attach-session", "-t", session_name])
        os.execvp("ssh", [
            "ssh", "-t",
            *SSH_OPTS,
            self._ssh_host,
            *tmux_cmd,
        ])
