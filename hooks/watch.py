#!/usr/bin/env python3
"""
Session watcher for Remote Claude.

Monitors tmux sessions and sends notifications when:
- Claude is asking for permission (interactive notification with buttons)
- Claude is waiting for user input (idle detection)
- A session has been idle for too long
- A session completes or errors

Usage:
  # Watch all sessions (runs in foreground)
  python3 watch.py

  # Watch specific session
  python3 watch.py --session rc-myproject-123456

  # Run as daemon
  python3 watch.py --daemon

  # One-shot check (useful for cron)
  python3 watch.py --once
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List

# Import from same directory
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))
from notify import send_notification, send_interactive_notification
from prompt_parser import (
    extract_prompt_from_output,
    parse_permission_prompt,
    format_notification_message,
    is_permission_prompt,
)


TMUX_SOCKET = "remote-claude"
CHECK_INTERVAL = 30  # seconds between checks
IDLE_THRESHOLD = 300  # seconds before considering session idle (5 min)
PROMPT_NOTIFY_DELAY = 120  # seconds to wait before notifying about permission prompts (2 min)
RATE_LIMIT_DEBOUNCE = 60  # seconds to wait before triggering rate limit action (avoid false positives)

# Patterns that indicate Claude is waiting for input
WAITING_PATTERNS = [
    r"^>",  # Input prompt
    r"^\?",  # Question prompt
    r"User:",  # Waiting for user message
    r"\[y/N\]",  # Confirmation prompt
    r"\[Y/n\]",
    r"Press Enter",
    r"waiting for input",
    r"Enter your",
]

# Patterns that indicate Claude is actively working
WORKING_PATTERNS = [
    r"Running",
    r"Executing",
    r"Building",
    r"Installing",
    r"Downloading",
    r"Compiling",
    r"Testing",
    r"\.\.\.",  # Progress indicator
    r"━",  # Progress bar
    r"⠋|⠙|⠹|⠸|⠼|⠴|⠦|⠧|⠇|⠏",  # Spinner
]

# Patterns that indicate rate limiting (case insensitive)
RATE_LIMIT_PATTERNS = [
    r"rate\s*limit",
    r"too\s+many\s+requests",
    r"429",
    r"quota\s*exceeded",
    r"capacity\s*exceeded",
    r"throttl",
    r"try\s+again\s+later",
    r"usage\s+limit",
]


class SessionState:
    """Track state of a session."""

    def __init__(self, name: str):
        self.name = name
        self.last_content: str = ""
        self.last_change: float = time.time()
        self.last_notification: float = 0
        self.is_waiting: bool = False
        self.notified_waiting: bool = False
        self.last_prompt: Optional[str] = None  # Last permission prompt detected
        self.notified_prompt: Optional[str] = None  # Prompt we already notified about
        self.prompt_detected_at: Optional[float] = None  # When current prompt was first seen
        # Rate limit tracking
        self.rate_limit_detected_at: Optional[float] = None  # When rate limit was first seen
        self.rate_limit_notified: bool = False  # Have we notified/acted on this rate limit?
        self.rate_limit_count: int = 0  # Count of consecutive rate limit detections


def run_tmux(args: List[str]) -> Optional[str]:
    """Run a tmux command and return output."""
    cmd = ["tmux", "-L", TMUX_SOCKET] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout
        return None
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


def get_sessions() -> List[str]:
    """Get list of remote-claude session names."""
    output = run_tmux([
        "list-sessions",
        "-F",
        "#{session_name}",
    ])
    if not output:
        return []

    return [
        name.strip()
        for name in output.strip().split("\n")
        if name.strip().startswith("rc-")
    ]


def capture_pane(session_name: str, lines: int = 50) -> Optional[str]:
    """Capture recent output from a session's pane."""
    output = run_tmux([
        "capture-pane",
        "-t", session_name,
        "-p",
        "-S", f"-{lines}",
    ])
    return output


def is_waiting_for_input(content: str) -> bool:
    """Check if the content indicates waiting for user input."""
    if not content:
        return False

    # Check last few lines
    lines = content.strip().split("\n")
    last_lines = "\n".join(lines[-10:]) if len(lines) > 10 else content

    # Check for waiting patterns
    for pattern in WAITING_PATTERNS:
        if re.search(pattern, last_lines, re.IGNORECASE | re.MULTILINE):
            return True

    return False


def is_actively_working(content: str) -> bool:
    """Check if Claude appears to be actively working."""
    if not content:
        return False

    lines = content.strip().split("\n")
    last_lines = "\n".join(lines[-5:]) if len(lines) > 5 else content

    for pattern in WORKING_PATTERNS:
        if re.search(pattern, last_lines):
            return True

    return False


def is_rate_limited(content: str) -> bool:
    """Check if the content indicates rate limiting."""
    if not content:
        return False

    # Check last 20 lines for rate limit indicators
    lines = content.strip().split("\n")
    last_lines = "\n".join(lines[-20:]) if len(lines) > 20 else content

    for pattern in RATE_LIMIT_PATTERNS:
        if re.search(pattern, last_lines, re.IGNORECASE):
            return True

    return False


def get_container_account(session_name: str) -> Optional[str]:
    """Get the account name for a session's container."""
    # Extract session_id from session name (rc-{session_id})
    if not session_name.startswith("rc-"):
        return None

    session_id = session_name[3:]  # Remove "rc-" prefix
    container_name = f"rc-{session_id}"

    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{index .Config.Labels \"rc.account\"}}", container_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass

    return None


def get_available_accounts() -> List[str]:
    """Get list of configured account names."""
    try:
        # Add parent directory to path to import lib.config
        lib_dir = Path(__file__).parent.parent
        sys.path.insert(0, str(lib_dir))
        from lib.config import load_config

        config = load_config()
        accounts = ["default"]
        accounts.extend(config.accounts.profiles.keys())
        return accounts
    except Exception:
        return ["default"]


def get_rate_limit_mode() -> str:
    """Get the configured rate limit handling mode."""
    try:
        lib_dir = Path(__file__).parent.parent
        sys.path.insert(0, str(lib_dir))
        from lib.config import load_config

        config = load_config()
        return config.accounts.on_rate_limit
    except Exception:
        return "manual"


def handle_rate_limit(notification: Dict) -> None:
    """Handle a rate limit notification based on configured mode.

    Args:
        notification: Rate limit notification dict with session, mode, accounts info
    """
    mode = notification.get("mode", "manual")
    session = notification.get("session", "")
    current_account = notification.get("current_account", "default")
    available_accounts = notification.get("available_accounts", [])

    if mode == "manual":
        # Just log it, user handles manually
        print(f"  Rate limit on '{current_account}'. Use 'rc switch {session} <account>' to switch.")
        return

    if mode == "notify":
        # Send notification suggesting switch
        if available_accounts:
            message = (
                f"Rate limit hit on account '{current_account}'.\n"
                f"Available accounts: {', '.join(available_accounts)}\n"
                f"Switch with: rc switch {session} {available_accounts[0]}"
            )
        else:
            message = f"Rate limit hit on account '{current_account}'. No other accounts configured."

        send_notification(
            title="Rate Limit - Switch Account?",
            message=message,
            session=session,
            priority="high",
        )
        return

    if mode == "auto":
        # Automatically switch to next available account
        if not available_accounts:
            print(f"  Auto-rotate failed: No other accounts configured")
            send_notification(
                title="Rate Limit - No Accounts",
                message=f"Rate limit on '{current_account}' but no other accounts configured.",
                session=session,
                priority="high",
            )
            return

        next_account = available_accounts[0]
        print(f"  Auto-rotating from '{current_account}' to '{next_account}'...")

        # Extract session_id from session name
        session_id = session[3:] if session.startswith("rc-") else session

        # Call rc switch
        try:
            rc_path = Path(__file__).parent.parent / "rc.py"
            result = subprocess.run(
                ["python3", str(rc_path), "switch", session_id, next_account],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                print(f"  Successfully switched to account '{next_account}'")
                send_notification(
                    title="Account Switched",
                    message=f"Auto-switched from '{current_account}' to '{next_account}' due to rate limit.",
                    session=session,
                    priority="normal",
                )
            else:
                print(f"  Switch failed: {result.stderr}")
                send_notification(
                    title="Auto-Switch Failed",
                    message=f"Failed to switch to '{next_account}': {result.stderr[:100]}",
                    session=session,
                    priority="high",
                )
        except Exception as e:
            print(f"  Switch error: {e}")
            send_notification(
                title="Auto-Switch Error",
                message=f"Error switching accounts: {str(e)[:100]}",
                session=session,
                priority="high",
            )


def check_session(state: SessionState) -> Optional[Dict]:
    """Check a session and return notification info if needed."""
    content = capture_pane(state.name)
    if content is None:
        return None

    now = time.time()

    # Check if content changed
    content_changed = content != state.last_content
    if content_changed:
        state.last_content = content
        state.last_change = now

    # Check for permission prompts first (these get interactive notifications)
    prompt_text = extract_prompt_from_output(content)
    if prompt_text:
        # Track when we first saw this prompt
        if prompt_text != state.last_prompt:
            state.last_prompt = prompt_text
            state.prompt_detected_at = now  # Start the idle timer

        # Only notify if:
        # 1. This is a prompt we haven't notified about
        # 2. It's been idle for PROMPT_NOTIFY_DELAY seconds (2 min by default)
        # 3. At least 30s since last notification
        if prompt_text != state.notified_prompt:
            idle_on_prompt = now - (state.prompt_detected_at or now)
            if idle_on_prompt >= PROMPT_NOTIFY_DELAY and now - state.last_notification > 30:
                parsed = parse_permission_prompt(prompt_text)
                if parsed:
                    state.notified_prompt = prompt_text
                    state.last_notification = now
                    state.notified_waiting = True  # Don't also send "waiting" notification

                    return {
                        "type": "permission",
                        "title": f"{parsed.tool} Permission",
                        "message": format_notification_message(parsed),
                        "session": state.name,
                        "parsed": parsed,
                        "priority": "high",
                    }
    else:
        # No permission prompt found - reset tracking
        state.last_prompt = None
        state.notified_prompt = None
        state.prompt_detected_at = None

    # Check for rate limiting
    rate_limited = is_rate_limited(content)
    if rate_limited:
        state.rate_limit_count += 1
        if state.rate_limit_detected_at is None:
            state.rate_limit_detected_at = now

        # Debounce: only act after sustained rate limit detection
        rate_limit_duration = now - state.rate_limit_detected_at
        if rate_limit_duration >= RATE_LIMIT_DEBOUNCE and not state.rate_limit_notified:
            state.rate_limit_notified = True
            state.last_notification = now

            current_account = get_container_account(state.name) or "default"
            available_accounts = get_available_accounts()
            other_accounts = [a for a in available_accounts if a != current_account]
            mode = get_rate_limit_mode()

            return {
                "type": "rate_limit",
                "title": "Rate Limit Detected",
                "message": f"Session {state.name} hit rate limit on account '{current_account}'",
                "session": state.name,
                "current_account": current_account,
                "available_accounts": other_accounts,
                "mode": mode,
                "priority": "high",
            }
    else:
        # Reset rate limit tracking if no longer rate limited
        state.rate_limit_detected_at = None
        state.rate_limit_notified = False
        state.rate_limit_count = 0

    # Determine if waiting for input (generic)
    waiting = is_waiting_for_input(content)
    working = is_actively_working(content)

    # Update waiting state
    was_waiting = state.is_waiting
    state.is_waiting = waiting and not working

    # Calculate idle time
    idle_time = now - state.last_change

    # Determine if we should notify
    should_notify = False
    notification = None

    # Notify if just started waiting (and not notified recently)
    # Skip if we already sent an interactive notification for a permission prompt
    if state.is_waiting and not state.notified_waiting:
        if now - state.last_notification > 60:  # At least 60s between notifications
            should_notify = True
            state.notified_waiting = True
            state.last_notification = now
            notification = {
                "type": "waiting",
                "title": "Claude Waiting",
                "message": f"Session {state.name} is waiting for input",
                "session": state.name,
                "priority": "normal",
            }

    # Notify if idle too long
    elif idle_time > IDLE_THRESHOLD and not state.is_waiting:
        if now - state.last_notification > IDLE_THRESHOLD:
            should_notify = True
            state.last_notification = now
            notification = {
                "type": "idle",
                "title": "Session Idle",
                "message": f"Session {state.name} has been idle for {int(idle_time/60)} minutes",
                "session": state.name,
                "priority": "low",
            }

    # Reset notified flag if no longer waiting
    if not state.is_waiting:
        state.notified_waiting = False

    return notification if should_notify else None


def watch_sessions(
    session_filter: Optional[str] = None,
    once: bool = False,
    verbose: bool = False,
):
    """Main watch loop."""
    states: Dict[str, SessionState] = {}

    print(f"Watching remote-claude sessions (interval: {CHECK_INTERVAL}s)")
    if session_filter:
        print(f"Filter: {session_filter}")
    print("Press Ctrl+C to stop\n")

    while True:
        try:
            # Get current sessions
            sessions = get_sessions()

            if session_filter:
                sessions = [s for s in sessions if session_filter in s]

            if verbose:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Checking {len(sessions)} session(s)")

            # Check each session
            for session_name in sessions:
                # Get or create state
                if session_name not in states:
                    states[session_name] = SessionState(session_name)
                    if verbose:
                        print(f"  New session: {session_name}")

                state = states[session_name]
                notification = check_session(state)

                if notification:
                    notif_type = notification.get("type", "generic")
                    print(f"  [{session_name}] {notification['title']}: {notification['message']}")

                    if notif_type == "permission":
                        # Send interactive notification with action buttons
                        send_interactive_notification(
                            title=notification["title"],
                            message=notification["message"],
                            session=session_name,
                        )
                    elif notif_type == "rate_limit":
                        # Handle rate limit based on configured mode
                        handle_rate_limit(notification)
                    else:
                        # Send regular notification
                        send_notification(
                            title=notification["title"],
                            message=notification["message"],
                            session=session_name,
                            priority=notification.get("priority", "normal"),
                        )
                elif verbose and state.is_waiting:
                    print(f"  [{session_name}] Waiting for input (already notified)")

            # Clean up states for removed sessions
            for name in list(states.keys()):
                if name not in sessions:
                    if verbose:
                        print(f"  Session ended: {name}")
                    del states[name]

            if once:
                break

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("\nStopping watcher")
            break


def run_daemon():
    """Run as a background daemon."""
    import signal

    # Write PID file
    pid_file = Path("/tmp/rc-watch.pid")
    pid_file.write_text(str(os.getpid()))

    def cleanup(signum, frame):
        pid_file.unlink(missing_ok=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    # Redirect output to log file
    log_file = Path("/tmp/rc-watch.log")
    sys.stdout = open(log_file, "a")
    sys.stderr = sys.stdout

    print(f"\n=== Watcher started at {datetime.now().isoformat()} ===")
    watch_sessions(verbose=True)


def main():
    global CHECK_INTERVAL, PROMPT_NOTIFY_DELAY

    parser = argparse.ArgumentParser(
        description="Watch Remote Claude sessions and send notifications",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--session", "-s",
        help="Watch specific session (partial match)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Check once and exit (useful for cron)",
    )
    parser.add_argument(
        "--daemon", "-d",
        action="store_true",
        help="Run as background daemon",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output",
    )
    parser.add_argument(
        "--interval", "-i",
        type=int,
        default=CHECK_INTERVAL,
        help=f"Check interval in seconds (default: {CHECK_INTERVAL})",
    )
    parser.add_argument(
        "--notify-delay",
        type=int,
        default=PROMPT_NOTIFY_DELAY,
        help=f"Seconds to wait before sending notification (default: {PROMPT_NOTIFY_DELAY})",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop running daemon",
    )

    args = parser.parse_args()
    CHECK_INTERVAL = args.interval
    PROMPT_NOTIFY_DELAY = args.notify_delay

    if args.stop:
        pid_file = Path("/tmp/rc-watch.pid")
        if pid_file.exists():
            pid = int(pid_file.read_text())
            try:
                os.kill(pid, 15)  # SIGTERM
                print(f"Stopped watcher (PID {pid})")
            except ProcessLookupError:
                print("Watcher not running")
            pid_file.unlink(missing_ok=True)
        else:
            print("No daemon running")
        return

    if args.daemon:
        # Fork to background
        if os.fork() > 0:
            print("Watcher started in background")
            print("  Log: /tmp/rc-watch.log")
            print("  PID: /tmp/rc-watch.pid")
            print("  Stop: python3 watch.py --stop")
            sys.exit(0)
        run_daemon()
    else:
        watch_sessions(
            session_filter=args.session,
            once=args.once,
            verbose=args.verbose,
        )


if __name__ == "__main__":
    main()
