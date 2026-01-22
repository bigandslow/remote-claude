#!/usr/bin/env python3
"""
Webhook notification hook for Remote Claude.

This script sends notifications when Claude needs user input.
It can be used as a Claude Code hook or called directly.

Usage as Claude hook (in ~/.claude/settings.json):
{
  "hooks": {
    "Notification": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /path/to/notify.py notification"
          }
        ]
      }
    ]
  }
}

Or call directly:
  python3 notify.py --title "Session Ready" --message "Claude needs input"
  python3 notify.py --webhook-url "https://..." --title "Alert"
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional


def load_config() -> dict:
    """Load notification config from remote-claude config file."""
    config_paths = [
        Path.home() / ".config" / "remote-claude" / "config.yaml",
        Path(__file__).parent.parent / "config" / "config.yaml.example",
    ]

    for config_path in config_paths:
        if config_path.exists():
            try:
                import yaml
                with open(config_path) as f:
                    config = yaml.safe_load(f)
                    return config.get("notifications", {})
            except ImportError:
                # Fallback: parse YAML manually for simple cases
                result = {"enabled": False}
                with open(config_path) as f:
                    content = f.read()
                    for line in content.split("\n"):
                        line = line.strip()
                        if line.startswith("enabled:") and "true" in line.lower():
                            result["enabled"] = True
                        elif line.startswith("webhook_url:") and "null" not in line and "YOUR_" not in line:
                            url = line.split(":", 1)[1].strip().strip('"\'')
                            if url:
                                result["webhook_url"] = url
                        elif line.startswith("pushover_user_key:") and "YOUR_" not in line:
                            key = line.split(":", 1)[1].strip().strip('"\'')
                            if key:
                                result["pushover_user_key"] = key
                        elif line.startswith("pushover_api_token:") and "YOUR_" not in line:
                            token = line.split(":", 1)[1].strip().strip('"\'')
                            if token:
                                result["pushover_api_token"] = token
                    return result
            except Exception:
                pass

    return {}


# Private IP ranges (SSRF protection)
_PRIVATE_IP_PREFIXES = (
    "10.",
    "172.16.", "172.17.", "172.18.", "172.19.",
    "172.20.", "172.21.", "172.22.", "172.23.",
    "172.24.", "172.25.", "172.26.", "172.27.",
    "172.28.", "172.29.", "172.30.", "172.31.",
    "192.168.",
    "127.",
    "169.254.",  # Link-local
    "0.",
)

_PRIVATE_HOSTNAMES = (
    "localhost",
    "localhost.localdomain",
    "local",
    "internal",
    "intranet",
)


def validate_webhook_url(url: str) -> tuple[bool, str]:
    """Validate a webhook URL for security.

    Checks:
    - Must be http or https (https preferred)
    - Must not point to private IP ranges
    - Must not point to localhost or internal hostnames

    Returns:
        Tuple of (is_valid, error_message). error_message is empty if valid.
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return (False, "Invalid URL format")

    # Check scheme
    if parsed.scheme not in ("http", "https"):
        return (False, f"Invalid URL scheme: {parsed.scheme}. Only http/https allowed.")

    if parsed.scheme == "http":
        print(f"Warning: Using insecure HTTP for webhook: {url}", file=sys.stderr)

    # Check hostname
    hostname = parsed.hostname
    if not hostname:
        return (False, "URL missing hostname")

    hostname_lower = hostname.lower()

    # Block known internal hostnames
    for private_host in _PRIVATE_HOSTNAMES:
        if hostname_lower == private_host or hostname_lower.endswith(f".{private_host}"):
            return (False, f"Webhook URL points to internal hostname: {hostname}")

    # Check if hostname looks like an IP address and if so, check if private
    import socket
    try:
        # Try to resolve the hostname to check for private IPs
        addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for info in addr_info:
            ip = info[4][0]
            for prefix in _PRIVATE_IP_PREFIXES:
                if ip.startswith(prefix):
                    return (False, f"Webhook URL resolves to private IP: {ip}")
    except socket.gaierror:
        # Can't resolve - might be fine, let the request fail later
        pass

    return (True, "")


def send_webhook(
    url: str,
    title: str,
    message: str,
    session: Optional[str] = None,
    workspace: Optional[str] = None,
    priority: str = "normal",
) -> bool:
    """Send a webhook notification.

    Supports multiple webhook formats:
    - Generic JSON POST
    - Slack-compatible
    - Discord-compatible
    - Pushover-compatible
    - ntfy.sh compatible

    Security: URLs are validated to prevent SSRF attacks.
    """
    # Validate URL to prevent SSRF
    is_valid, error = validate_webhook_url(url)
    if not is_valid:
        print(f"Webhook URL validation failed: {error}", file=sys.stderr)
        return False

    timestamp = datetime.now().isoformat()
    hostname = os.uname().nodename

    # Build payload based on URL patterns
    if "slack.com" in url or "hooks.slack.com" in url:
        # Slack webhook format
        payload = {
            "text": f"*{title}*\n{message}",
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": title}
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": message}
                },
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": f"*Host:* {hostname}"},
                        {"type": "mrkdwn", "text": f"*Session:* {session or 'N/A'}"},
                    ]
                }
            ]
        }
    elif "discord.com" in url or "discordapp.com" in url:
        # Discord webhook format
        payload = {
            "embeds": [{
                "title": title,
                "description": message,
                "color": 5814783,  # Blue color
                "fields": [
                    {"name": "Host", "value": hostname, "inline": True},
                    {"name": "Session", "value": session or "N/A", "inline": True},
                ],
                "timestamp": timestamp,
            }]
        }
    elif "ntfy.sh" in url or "ntfy" in url:
        # ntfy.sh format (simpler)
        payload = {
            "topic": url.split("/")[-1] if "/" in url else "remote-claude",
            "title": title,
            "message": message,
            "priority": 4 if priority == "high" else 3,
            "tags": ["robot", "computer"],
        }
        # ntfy uses the topic in the URL
        if "ntfy.sh" in url and not url.endswith("/"):
            url = url.rstrip("/")
    elif "api.pushover.net" in url:
        # Pushover format
        payload = {
            "title": title,
            "message": message,
            "priority": 1 if priority == "high" else 0,
        }
    else:
        # Generic JSON payload
        payload = {
            "title": title,
            "message": message,
            "timestamp": timestamp,
            "hostname": hostname,
            "session": session,
            "workspace": workspace,
            "priority": priority,
            "source": "remote-claude",
        }

    # Send the request
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "remote-claude/1.0",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status == 200 or response.status == 204

    except urllib.error.HTTPError as e:
        print(f"HTTP Error: {e.code} - {e.reason}", file=sys.stderr)
        return False
    except urllib.error.URLError as e:
        print(f"URL Error: {e.reason}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error sending notification: {e}", file=sys.stderr)
        return False


def send_pushover(
    user_key: str,
    api_token: str,
    title: str,
    message: str,
    priority: str = "normal",
    url: Optional[str] = None,
    url_title: Optional[str] = None,
    actions: Optional[list] = None,
) -> bool:
    """Send a notification via Pushover API.

    Args:
        user_key: Pushover user key
        api_token: Pushover API token
        title: Notification title
        message: Notification message
        priority: low, normal, or high
        url: URL to open when notification is tapped
        url_title: Title for the URL
        actions: List of action dicts with 'label' and 'url' keys (max 3)
    """
    api_url = "https://api.pushover.net/1/messages.json"

    # Map priority to Pushover values: -2 to 2
    priority_map = {"low": -1, "normal": 0, "high": 1}
    pushover_priority = priority_map.get(priority, 0)

    payload = {
        "token": api_token,
        "user": user_key,
        "title": title,
        "message": message,
        "priority": pushover_priority,
    }

    # Add main URL (opens when notification body is tapped)
    if url:
        payload["url"] = url
        if url_title:
            payload["url_title"] = url_title

    # Add supplementary actions (buttons)
    # Format: action=label,url (up to 3)
    if actions:
        for i, action in enumerate(actions[:3]):
            # Pushover expects: supplementary_url, supplementary_url_title
            # Or for actions: action=inline,label,url
            # Using inline actions for buttons
            pass  # Pushover's action format is complex, using supplementary URLs instead

    try:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(api_url, data=data, method="POST")

        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status == 200

    except urllib.error.HTTPError as e:
        print(f"Pushover HTTP Error: {e.code} - {e.reason}", file=sys.stderr)
        return False
    except urllib.error.URLError as e:
        print(f"Pushover URL Error: {e.reason}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Pushover Error: {e}", file=sys.stderr)
        return False


def get_responder_config() -> dict:
    """Get responder configuration from config file."""
    config_path = Path.home() / ".config" / "remote-claude" / "config.yaml"
    defaults = {
        "host": None,  # Will auto-detect Tailscale IP
        "port": 8422,
        "blink_user": os.environ.get("USER", "user"),
        "blink_host": None,  # Will use Tailscale hostname
    }

    if config_path.exists():
        try:
            import yaml
            with open(config_path) as f:
                config = yaml.safe_load(f)
                responder = config.get("responder", {})
                defaults.update(responder)
        except Exception:
            pass

    return defaults


def generate_action_token(session: str, action: str) -> str:
    """Generate a signed token for an action.

    This imports from responder.py to use the same signing logic.
    """
    try:
        script_dir = Path(__file__).parent
        sys.path.insert(0, str(script_dir))
        from responder import generate_token
        return generate_token(session, action)
    except ImportError:
        # Fallback: generate simple token (less secure)
        import hashlib
        import time
        timestamp = int(time.time())
        data = f"{session}:{action}:{timestamp}"
        return hashlib.sha256(data.encode()).hexdigest()[:32]


def get_blink_url(session: str, user: str, host: str) -> str:
    """Generate a Blink Shell deep link URL to attach to a session.

    Format: blinkshell://run?cmd=mosh user@host -- tmux -L remote-claude attach -t session
    """
    cmd = f"mosh {user}@{host} -- tmux -L remote-claude attach -t {session}"
    encoded_cmd = urllib.parse.quote(cmd)
    return f"blinkshell://run?cmd={encoded_cmd}"


def send_interactive_notification(
    title: str,
    message: str,
    session: str,
    responder_host: Optional[str] = None,
    responder_port: int = 8422,
    blink_user: Optional[str] = None,
    blink_host: Optional[str] = None,
) -> bool:
    """Send an interactive notification with action buttons.

    The notification will have:
    - Tap body: Opens Blink Shell attached to the session
    - Button 1: Yes (sends 'y')
    - Button 2: Always (sends '!')
    - Button 3: No (sends 'n')
    """
    config = load_config()

    if not config.get("enabled", False):
        return True

    pushover_user = os.environ.get("PUSHOVER_USER_KEY") or config.get("pushover_user_key")
    pushover_token = os.environ.get("PUSHOVER_API_TOKEN") or config.get("pushover_api_token")

    if not pushover_user or not pushover_token:
        print("Pushover not configured for interactive notifications", file=sys.stderr)
        return False

    # Get responder config
    resp_config = get_responder_config()
    host = responder_host or resp_config.get("host")
    port = responder_port or resp_config.get("port", 8422)
    user = blink_user or resp_config.get("blink_user", os.environ.get("USER", "user"))
    blink_h = blink_host or resp_config.get("blink_host")

    # Auto-detect Tailscale hostname if not set
    if not host or not blink_h:
        # Try tailscale CLI first
        try:
            result = subprocess.run(
                ["tailscale", "status", "--json"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                status = json.loads(result.stdout)
                self_node = status.get("Self", {})
                if not host:
                    host = self_node.get("TailscaleIPs", [None])[0]
                if not blink_h:
                    blink_h = self_node.get("DNSName", "").rstrip(".")
        except Exception:
            pass

        # Fallback: check network interfaces for Tailscale IP (100.x.x.x range)
        if not host:
            try:
                import re
                result = subprocess.run(
                    ["/sbin/ifconfig"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    for match in re.finditer(r'inet (100\.\d+\.\d+\.\d+)', result.stdout):
                        host = match.group(1)
                        break
            except Exception:
                pass

    if not host:
        print("Could not determine responder host (Tailscale not running?)", file=sys.stderr)
        return False

    if not blink_h:
        blink_h = host  # Fall back to IP

    # Generate Blink URL for main tap action
    blink_url = get_blink_url(session, user, blink_h)

    # Generate signed tokens for each action
    yes_token = generate_action_token(session, "yes")
    always_token = generate_action_token(session, "always")
    no_token = generate_action_token(session, "no")

    # Build action URLs
    base_url = f"http://{host}:{port}/respond"
    yes_url = f"{base_url}?token={yes_token}"
    always_url = f"{base_url}?token={always_token}"
    no_url = f"{base_url}?token={no_token}"

    # Build HTML message with clickable action links
    html_message = (
        f"{message}<br><br>"
        f"<a href=\"{yes_url}\">✓ Yes</a> &nbsp; "
        f"<a href=\"{always_url}\">✓ Always</a> &nbsp; "
        f"<a href=\"{no_url}\">✗ No</a>"
    )

    # Send with HTML enabled and main URL for Blink
    api_url = "https://api.pushover.net/1/messages.json"

    payload = {
        "token": pushover_token,
        "user": pushover_user,
        "title": title,
        "message": html_message,
        "html": 1,  # Enable HTML formatting
        "url": blink_url,
        "url_title": "Open Terminal",
        "priority": 1,  # High priority
    }

    try:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(api_url, data=data, method="POST")

        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status == 200

    except Exception as e:
        print(f"Pushover Error: {e}", file=sys.stderr)
        return False


def send_notification(
    title: str,
    message: str,
    webhook_url: Optional[str] = None,
    session: Optional[str] = None,
    workspace: Optional[str] = None,
    priority: str = "normal",
) -> bool:
    """Send a notification via configured service (Pushover or webhook)."""

    config = load_config()

    if not config.get("enabled", False):
        return True  # Silently succeed if notifications disabled

    # Check for Pushover configuration first
    pushover_user = os.environ.get("PUSHOVER_USER_KEY") or config.get("pushover_user_key")
    pushover_token = os.environ.get("PUSHOVER_API_TOKEN") or config.get("pushover_api_token")

    if pushover_user and pushover_token:
        # Add session info to message if provided
        full_message = message
        if session:
            full_message = f"{message}\nSession: {session}"
        return send_pushover(pushover_user, pushover_token, title, full_message, priority)

    # Fall back to webhook URL
    url = webhook_url or os.environ.get("RC_WEBHOOK_URL") or config.get("webhook_url")

    if not url:
        print("No notification method configured (need Pushover credentials or webhook URL)", file=sys.stderr)
        return False

    return send_webhook(url, title, message, session, workspace, priority)


def handle_claude_hook():
    """Handle being called as a Claude Code hook.

    Claude hooks receive context via stdin as JSON.
    """
    # Read hook context from stdin
    try:
        stdin_data = sys.stdin.read()
        if stdin_data:
            context = json.loads(stdin_data)
        else:
            context = {}
    except json.JSONDecodeError:
        context = {}

    # Extract relevant information
    hook_type = context.get("hook_type", "unknown")
    session_id = context.get("session_id", os.environ.get("RC_SESSION_ID", "unknown"))
    workspace = context.get("cwd", os.environ.get("RC_WORKSPACE", "unknown"))

    # Determine notification content based on hook type
    if hook_type == "Notification":
        title = "Claude Notification"
        message = context.get("message", "Claude has a notification")
    elif hook_type == "Stop":
        title = "Claude Session Ended"
        message = f"Session {session_id} has stopped"
    else:
        title = "Claude Alert"
        message = f"Hook triggered: {hook_type}"

    # Send the notification
    success = send_notification(
        title=title,
        message=message,
        session=session_id,
        workspace=workspace,
    )

    sys.exit(0 if success else 1)


def main():
    parser = argparse.ArgumentParser(
        description="Send webhook notifications for Remote Claude",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Send a simple notification
  %(prog)s --title "Session Ready" --message "Claude needs input"

  # Use a specific webhook URL
  %(prog)s --webhook-url "https://ntfy.sh/mytopic" --title "Alert"

  # High priority notification
  %(prog)s --title "Urgent" --message "Review needed" --priority high

  # Called as Claude hook (reads context from stdin)
  %(prog)s notification
        """,
    )

    parser.add_argument(
        "hook_type",
        nargs="?",
        help="Hook type when called as Claude hook (notification, stop, etc.)",
    )
    parser.add_argument(
        "--title", "-t",
        default="Remote Claude",
        help="Notification title",
    )
    parser.add_argument(
        "--message", "-m",
        default="Notification from Claude session",
        help="Notification message",
    )
    parser.add_argument(
        "--webhook-url", "-u",
        help="Webhook URL (overrides config)",
    )
    parser.add_argument(
        "--session", "-s",
        help="Session identifier",
    )
    parser.add_argument(
        "--workspace", "-w",
        help="Workspace path",
    )
    parser.add_argument(
        "--priority", "-p",
        choices=["low", "normal", "high"],
        default="normal",
        help="Notification priority",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Send a test notification",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Send an interactive notification with action buttons (requires --session)",
    )
    parser.add_argument(
        "--responder-host",
        help="Responder host (default: auto-detect Tailscale IP)",
    )
    parser.add_argument(
        "--responder-port",
        type=int,
        default=8422,
        help="Responder port (default: 8422)",
    )

    args = parser.parse_args()

    # If called as a hook, handle specially
    if args.hook_type:
        handle_claude_hook()
        return

    # Test mode
    if args.test:
        args.title = "Test Notification"
        args.message = f"This is a test from Remote Claude at {datetime.now().strftime('%H:%M:%S')}"

    # Interactive mode
    if args.interactive:
        if not args.session:
            args.session = "rc-test-session"  # Use test session for demo
        success = send_interactive_notification(
            title=args.title or "Claude Permission Request",
            message=args.message or "Allow this action?",
            session=args.session,
            responder_host=args.responder_host,
            responder_port=args.responder_port,
        )
    else:
        # Send regular notification
        success = send_notification(
            title=args.title,
            message=args.message,
            webhook_url=args.webhook_url,
            session=args.session,
            workspace=args.workspace,
            priority=args.priority,
        )

    if success:
        print("Notification sent successfully")
    else:
        print("Failed to send notification", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
