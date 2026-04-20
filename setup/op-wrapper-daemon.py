#!/usr/bin/env python3
"""
op-wrapper daemon — 1Password CLI proxy with caching.

Listens on a Unix socket and a TCP port. Receives EXEC commands, runs `op read`,
caches results in memory, and returns base64-encoded values.

Only `op read` is permitted; other commands return ERROR EXEC_DENIED.

Wire protocol (one command per connection, line-terminated):
    EXEC read <uri>

Response:
    OK <base64-encoded-value>
    ERROR <message>
"""
import argparse
import base64
import hashlib
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

CACHE_TTL = 600  # seconds


class Cache:
    def __init__(self) -> None:
        self._data: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expires, value = entry
            if time.time() >= expires:
                del self._data[key]
                return None
            return value

    def set(self, key: str, value: str, ttl: int = CACHE_TTL) -> None:
        with self._lock:
            self._data[key] = (time.time() + ttl, value)

    def size(self) -> int:
        with self._lock:
            return len(self._data)


def exec_read(uri: str, cache: Cache) -> bytes:
    """Run `op read <uri>` with caching. Returns raw response bytes (with trailing newline)."""
    key = hashlib.md5(f"read {uri}".encode()).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return f"OK {cached}\n".encode()

    try:
        result = subprocess.run(
            ["op", "read", uri],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return b"ERROR OP_TIMEOUT op read timed out after 30s\n"
    except FileNotFoundError:
        return b"ERROR OP_NOT_FOUND op CLI not installed on host\n"

    if result.returncode != 0:
        # Strip newlines from stderr so it's a single line
        err = result.stderr.strip().replace("\n", " ")
        return f"ERROR OP_FAILED {err}\n".encode()

    encoded = base64.b64encode(result.stdout.rstrip("\n").encode()).decode()
    cache.set(key, encoded)
    return f"OK {encoded}\n".encode()


def handle_command(line: str, cache: Cache) -> bytes:
    """Parse one command line and return the response bytes."""
    parts = line.split(" ", 2)
    if not parts:
        return b"ERROR EMPTY\n"

    cmd = parts[0]
    if cmd != "EXEC":
        return b"ERROR UNKNOWN_CMD\n"

    if len(parts) < 3:
        return b"ERROR BAD_REQUEST\n"

    subcmd = parts[1]
    uri = parts[2].rstrip()

    if subcmd != "read":
        return b"ERROR EXEC_DENIED\n"

    return exec_read(uri, cache)


def handle_connection(conn: socket.socket, cache: Cache) -> None:
    """Read one command from the connection and respond."""
    try:
        # Read until newline
        buf = b""
        while b"\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
            if len(buf) > 4096:
                conn.sendall(b"ERROR TOO_LONG\n")
                return

        line = buf.split(b"\n", 1)[0].decode("utf-8", errors="replace")
        response = handle_command(line, cache)
        conn.sendall(response)
    except (OSError, UnicodeDecodeError):
        pass
    finally:
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        conn.close()


def serve_listener(listener: socket.socket, cache: Cache, label: str) -> None:
    """Accept loop — spawns a thread per connection."""
    while True:
        try:
            conn, _ = listener.accept()
        except OSError:
            break
        t = threading.Thread(
            target=handle_connection,
            args=(conn, cache),
            daemon=True,
        )
        t.start()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--socket",
        default=str(Path.home() / ".op-wrapper" / "sock" / "daemon.sock"),
        help="Unix socket path (for op-wrapper client on host)",
    )
    parser.add_argument(
        "--tcp-port",
        type=int,
        default=2626,
        help="TCP port (for Docker containers via host.docker.internal)",
    )
    parser.add_argument(
        "--tcp-host",
        default="127.0.0.1",
        help="TCP bind address",
    )
    args = parser.parse_args()

    socket_path = Path(args.socket)
    socket_path.parent.mkdir(parents=True, exist_ok=True)

    # Remove stale socket
    if socket_path.exists():
        socket_path.unlink()

    cache = Cache()

    # Unix socket listener
    unix_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    unix_listener.bind(str(socket_path))
    os.chmod(str(socket_path), 0o600)
    unix_listener.listen(32)

    # TCP listener
    tcp_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp_listener.bind((args.tcp_host, args.tcp_port))
    tcp_listener.listen(32)

    print(f"[daemon] Unix socket: {socket_path}")
    print(f"[daemon] TCP: {args.tcp_host}:{args.tcp_port}")
    sys.stdout.flush()

    # Serve both in separate threads
    t_unix = threading.Thread(target=serve_listener, args=(unix_listener, cache, "unix"), daemon=True)
    t_tcp = threading.Thread(target=serve_listener, args=(tcp_listener, cache, "tcp"), daemon=True)
    t_unix.start()
    t_tcp.start()

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            unix_listener.close()
            tcp_listener.close()
            socket_path.unlink(missing_ok=True)
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
