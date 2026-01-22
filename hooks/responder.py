#!/usr/bin/env python3
"""
HTTP Responder for Remote Claude.

Receives authenticated requests from Pushover notification buttons
and sends keystrokes to tmux sessions.

Security:
- Binds to Tailscale IP only (not accessible from public internet)
- Validates HMAC-signed tokens with expiration
- Single-use tokens (replay protection)
- All requests logged

Usage:
  # Start responder (auto-detects Tailscale IP)
  python3 responder.py

  # Start on specific interface
  python3 responder.py --host 100.x.x.x

  # Start on localhost for testing
  python3 responder.py --host 127.0.0.1

  # Run as daemon
  python3 responder.py --daemon
"""

import argparse
import base64
import hashlib
import hmac
import http.server
import json
import os
import secrets
import socketserver
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

# Configuration
DEFAULT_PORT = 8422
TOKEN_EXPIRY_SECONDS = 300  # 5 minutes
TMUX_SOCKET = "remote-claude"

# Secret key for signing tokens (generated on first run, stored in config)
SECRET_KEY_FILE = Path.home() / ".config" / "remote-claude" / ".responder_secret"

# Used tokens (for replay protection)
USED_TOKENS: set = set()
USED_TOKENS_CLEANUP_INTERVAL = 600  # Clean up old tokens every 10 min
LAST_CLEANUP = time.time()

# Rate limiting
RATE_LIMIT_REQUESTS = 10  # Max requests per window
RATE_LIMIT_WINDOW = 60  # Window in seconds
_rate_limit_tracker: dict[str, list[float]] = {}  # IP -> list of timestamps
_rate_limit_last_cleanup = time.time()


def check_rate_limit(client_ip: str) -> bool:
    """Check if client IP is within rate limit.

    Returns True if request should be allowed, False if rate limited.
    """
    global _rate_limit_last_cleanup

    now = time.time()

    # Periodic cleanup of old entries (every 5 minutes)
    if now - _rate_limit_last_cleanup > 300:
        cutoff = now - RATE_LIMIT_WINDOW * 2
        for ip in list(_rate_limit_tracker.keys()):
            _rate_limit_tracker[ip] = [t for t in _rate_limit_tracker[ip] if t > cutoff]
            if not _rate_limit_tracker[ip]:
                del _rate_limit_tracker[ip]
        _rate_limit_last_cleanup = now

    # Get request history for this IP
    if client_ip not in _rate_limit_tracker:
        _rate_limit_tracker[client_ip] = []

    # Filter to requests within the current window
    window_start = now - RATE_LIMIT_WINDOW
    recent_requests = [t for t in _rate_limit_tracker[client_ip] if t > window_start]
    _rate_limit_tracker[client_ip] = recent_requests

    # Check if over limit
    if len(recent_requests) >= RATE_LIMIT_REQUESTS:
        return False

    # Record this request
    _rate_limit_tracker[client_ip].append(now)
    return True


def get_secret_key() -> bytes:
    """Get or generate the secret key for signing tokens."""
    if SECRET_KEY_FILE.exists():
        return SECRET_KEY_FILE.read_bytes()

    # Generate new secret key
    SECRET_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    SECRET_KEY_FILE.write_bytes(key)
    SECRET_KEY_FILE.chmod(0o600)
    return key


SECRET_KEY = get_secret_key()


def generate_token(session: str, action: str, timestamp: Optional[float] = None) -> str:
    """Generate a signed token for an action.

    Token format: base64(session:action:timestamp:signature)
    """
    if timestamp is None:
        timestamp = time.time()

    # Create message to sign
    message = f"{session}:{action}:{timestamp:.0f}"

    # Sign with HMAC-SHA256
    signature = hmac.new(SECRET_KEY, message.encode(), hashlib.sha256).hexdigest()[:16]

    # Encode token
    token_data = f"{message}:{signature}"
    token = base64.urlsafe_b64encode(token_data.encode()).decode()

    return token


def validate_token(token: str) -> Tuple[bool, Optional[str], Optional[str], str]:
    """Validate a token and return (valid, session, action, error_message).

    Returns:
        (True, session, action, "") if valid
        (False, None, None, error_message) if invalid
    """
    global USED_TOKENS, LAST_CLEANUP

    # Cleanup old tokens periodically
    now = time.time()
    if now - LAST_CLEANUP > USED_TOKENS_CLEANUP_INTERVAL:
        USED_TOKENS = set()  # Simple cleanup - just clear all
        LAST_CLEANUP = now

    try:
        # Decode token
        token_data = base64.urlsafe_b64decode(token.encode()).decode()
        parts = token_data.split(":")

        if len(parts) != 4:
            return False, None, None, "Invalid token format"

        session, action, timestamp_str, provided_sig = parts
        timestamp = float(timestamp_str)

        # Check expiration
        if now - timestamp > TOKEN_EXPIRY_SECONDS:
            return False, None, None, "Token expired"

        # Check if already used
        if token in USED_TOKENS:
            return False, None, None, "Token already used"

        # Verify signature
        message = f"{session}:{action}:{timestamp_str}"
        expected_sig = hmac.new(SECRET_KEY, message.encode(), hashlib.sha256).hexdigest()[:16]

        if not hmac.compare_digest(provided_sig, expected_sig):
            return False, None, None, "Invalid signature"

        # Mark token as used
        USED_TOKENS.add(token)

        return True, session, action, ""

    except Exception as e:
        return False, None, None, f"Token validation error: {e}"


def get_tailscale_ip() -> Optional[str]:
    """Get the Tailscale IP address of this machine."""
    # Try tailscale CLI first
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass

    # Fallback: check network interfaces for Tailscale IP (100.x.x.x range)
    try:
        result = subprocess.run(
            ["/sbin/ifconfig"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            import re
            # Look for 100.x.x.x addresses (Tailscale CGNAT range)
            for match in re.finditer(r'inet (100\.\d+\.\d+\.\d+)', result.stdout):
                return match.group(1)
    except Exception:
        pass

    return None


def send_tmux_keys(session: str, keys: str) -> bool:
    """Send keystrokes to a tmux session."""
    try:
        result = subprocess.run(
            ["tmux", "-L", TMUX_SOCKET, "send-keys", "-t", session, keys, "Enter"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception as e:
        print(f"Error sending keys to tmux: {e}", file=sys.stderr)
        return False


def session_exists(session: str) -> bool:
    """Check if a tmux session exists."""
    try:
        result = subprocess.run(
            ["tmux", "-L", TMUX_SOCKET, "has-session", "-t", session],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


# Action to keystroke mapping
ACTION_KEYS = {
    "yes": "y",
    "no": "n",
    "always": "!",
    "skip": "s",
    "abort": "a",
}


class ResponderHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler for responder."""

    def log_message(self, format, *args):
        """Custom logging."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {self.address_string()} - {format % args}")

    def send_json_response(self, status: int, data: dict):
        """Send a JSON response."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_GET(self):
        """Handle GET requests."""
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        # Health check endpoint (exempt from rate limiting)
        if parsed.path == "/health":
            self.send_json_response(200, {"status": "ok"})
            return

        # Rate limit check for all other endpoints
        client_ip = self.client_address[0]
        if not check_rate_limit(client_ip):
            self.log_message(f"Rate limited: {client_ip}")
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Retry-After", str(RATE_LIMIT_WINDOW))
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Too many requests"}).encode())
            return

        # Respond endpoint
        if parsed.path == "/respond":
            self.handle_respond(params)
            return

        # Unknown endpoint
        self.send_json_response(404, {"error": "Not found"})

    def handle_respond(self, params: dict):
        """Handle a respond request."""
        # Get token
        token = params.get("token", [None])[0]
        if not token:
            self.send_json_response(400, {"error": "Missing token"})
            return

        # Validate token
        valid, session, action, error = validate_token(token)
        if not valid:
            self.log_message(f"Token validation failed: {error}")
            self.send_json_response(403, {"error": error})
            return

        # Check session exists
        if not session_exists(session):
            self.send_json_response(404, {"error": f"Session not found: {session}"})
            return

        # Get keystroke for action
        keys = ACTION_KEYS.get(action)
        if not keys:
            self.send_json_response(400, {"error": f"Unknown action: {action}"})
            return

        # Send keys to tmux
        if send_tmux_keys(session, keys):
            self.log_message(f"Sent '{keys}' to session {session} (action: {action})")
            self.send_json_response(200, {
                "status": "ok",
                "session": session,
                "action": action,
            })
        else:
            self.send_json_response(500, {"error": "Failed to send keys to tmux"})


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """HTTP server with threading support."""
    allow_reuse_address = True


def run_server(host: str, port: int):
    """Run the HTTP responder server."""
    server = ThreadedHTTPServer((host, port), ResponderHandler)

    print(f"Responder server starting on {host}:{port}")
    print(f"Token expiry: {TOKEN_EXPIRY_SECONDS} seconds")
    print("")
    print("Endpoints:")
    print(f"  GET http://{host}:{port}/health - Health check")
    print(f"  GET http://{host}:{port}/respond?token=XXX - Handle action")
    print("")
    print("Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down")
        server.shutdown()


def run_daemon(host: str, port: int):
    """Run as a background daemon."""
    pid_file = Path("/tmp/rc-responder.pid")
    log_file = Path("/tmp/rc-responder.log")

    # Fork to background
    if os.fork() > 0:
        print(f"Responder started in background on {host}:{port}")
        print(f"  Log: {log_file}")
        print(f"  PID: {pid_file}")
        print(f"  Stop: python3 responder.py --stop")
        sys.exit(0)

    # Write PID
    pid_file.write_text(str(os.getpid()))

    # Redirect output
    sys.stdout = open(log_file, "a")
    sys.stderr = sys.stdout

    print(f"\n=== Responder started at {datetime.now().isoformat()} ===")
    run_server(host, port)


def main():
    parser = argparse.ArgumentParser(
        description="HTTP responder for Remote Claude notifications",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start on Tailscale IP (auto-detected)
  %(prog)s

  # Start on localhost for testing
  %(prog)s --host 127.0.0.1

  # Run as daemon
  %(prog)s --daemon

  # Generate a test token
  %(prog)s --gen-token --session rc-test-123 --action yes
        """,
    )

    parser.add_argument(
        "--host",
        help="Host to bind to (default: Tailscale IP or localhost)",
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to bind to (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--daemon", "-d",
        action="store_true",
        help="Run as background daemon",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop running daemon",
    )
    parser.add_argument(
        "--gen-token",
        action="store_true",
        help="Generate a test token",
    )
    parser.add_argument(
        "--session",
        help="Session name (for --gen-token)",
    )
    parser.add_argument(
        "--action",
        choices=list(ACTION_KEYS.keys()),
        help="Action (for --gen-token)",
    )
    parser.add_argument(
        "--allow-localhost",
        action="store_true",
        help="Allow binding to localhost when Tailscale unavailable (less secure)",
    )

    args = parser.parse_args()

    # Stop daemon
    if args.stop:
        pid_file = Path("/tmp/rc-responder.pid")
        if pid_file.exists():
            pid = int(pid_file.read_text())
            try:
                os.kill(pid, 15)
                print(f"Stopped responder (PID {pid})")
            except ProcessLookupError:
                print("Responder not running")
            pid_file.unlink(missing_ok=True)
        else:
            print("No daemon running")
        return

    # Generate token
    if args.gen_token:
        if not args.session or not args.action:
            print("Error: --session and --action required with --gen-token")
            sys.exit(1)
        token = generate_token(args.session, args.action)
        print(f"Token: {token}")
        print(f"URL: http://HOST:{args.port}/respond?token={token}")
        return

    # Determine host (with retry for LaunchAgent startup)
    host = args.host
    if not host:
        # Try a few times in case Tailscale isn't ready yet
        for attempt in range(5):
            host = get_tailscale_ip()
            if host:
                print(f"Using Tailscale IP: {host}", flush=True)
                break
            if attempt < 4:
                print(f"Waiting for Tailscale... (attempt {attempt + 1}/5)", flush=True)
                time.sleep(2)

        if not host:
            if not args.allow_localhost:
                print("ERROR: Tailscale not found and --allow-localhost not specified.", file=sys.stderr)
                print("", file=sys.stderr)
                print("Security Warning:", file=sys.stderr)
                print("  Binding to localhost is less secure than Tailscale.", file=sys.stderr)
                print("  Other processes on this machine could access the responder.", file=sys.stderr)
                print("", file=sys.stderr)
                print("Options:", file=sys.stderr)
                print("  1. Install and connect Tailscale for secure remote access", file=sys.stderr)
                print("  2. Use --allow-localhost to accept the security risk", file=sys.stderr)
                print("  3. Use --host to specify a specific interface", file=sys.stderr)
                sys.exit(1)

            host = "127.0.0.1"
            print("WARNING: Tailscale not found, falling back to localhost.", flush=True)
            print("WARNING: This is less secure - other processes on this machine can access the responder.", flush=True)
            print(f"Binding to: {host}", flush=True)

    # Run server
    if args.daemon:
        run_daemon(host, args.port)
    else:
        run_server(host, args.port)


if __name__ == "__main__":
    main()
