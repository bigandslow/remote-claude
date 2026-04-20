#!/bin/bash
#
# git-remote-auth - Switch git remote URL between authenticated (container)
# and plain HTTPS (host) formats.
#
# Inside a container: injects GH_TOKEN into the remote URL for push access.
# Outside a container: strips any embedded credentials from the remote URL.
#
# Usage:
#   ./git-remote-auth.sh              # auto-detect container vs host
#   ./git-remote-auth.sh --container  # force container mode
#   ./git-remote-auth.sh --host       # force host mode
#   ./git-remote-auth.sh --status     # show current remote and detected mode
#
# Place this script in your project's .rc/ directory and add to setup_commands
# in .rc/project.yaml:
#
#   setup_commands:
#     - bash .rc/git-remote-auth.sh

set -euo pipefail

REMOTE="${GIT_REMOTE:-origin}"

is_container() {
    [ -f /.dockerenv ] || grep -q 'docker\|containerd' /proc/1/cgroup 2>/dev/null
}

get_mode() {
    if [ "${1:-}" = "--container" ]; then
        echo "container"
    elif [ "${1:-}" = "--host" ]; then
        echo "host"
    elif is_container; then
        echo "container"
    else
        echo "host"
    fi
}

current_url() {
    git remote get-url "$REMOTE" 2>/dev/null
}

# Strip any embedded credentials from an HTTPS URL
# https://user:token@github.com/org/repo.git → https://github.com/org/repo.git
strip_credentials() {
    local url="$1"
    echo "$url" | sed -E 's|https://[^@]+@|https://|'
}

# Inject token into an HTTPS URL
# https://github.com/org/repo.git → https://x-access-token:TOKEN@github.com/org/repo.git
inject_token() {
    local url="$1"
    local token="$2"
    # Strip any existing credentials first
    local clean
    clean=$(strip_credentials "$url")
    echo "$clean" | sed -E "s|https://|https://x-access-token:${token}@|"
}

MODE=$(get_mode "${1:-}")
URL=$(current_url)

if [ -z "$URL" ]; then
    echo "Error: no remote '$REMOTE' found" >&2
    exit 1
fi

if [ "${1:-}" = "--status" ]; then
    echo "Remote:    $REMOTE"
    echo "URL:       $URL"
    if is_container; then
        echo "Detected:  container"
    else
        echo "Detected:  host"
    fi
    if echo "$URL" | grep -qE 'https://[^@]+@'; then
        echo "Auth:      credentials embedded"
    elif echo "$URL" | grep -q 'https://'; then
        echo "Auth:      no credentials (plain HTTPS)"
    else
        echo "Auth:      SSH or other"
    fi
    exit 0
fi

# Only operate on HTTPS URLs
if ! echo "$URL" | grep -q '^https://'; then
    echo "Remote '$REMOTE' is not HTTPS ($URL), nothing to do."
    exit 0
fi

if [ "$MODE" = "container" ]; then
    TOKEN="${GH_TOKEN:-}"
    if [ -z "$TOKEN" ]; then
        echo "Error: GH_TOKEN not set. Cannot inject credentials." >&2
        exit 1
    fi
    NEW_URL=$(inject_token "$URL" "$TOKEN")
    if [ "$URL" = "$NEW_URL" ]; then
        echo "Remote already has credentials."
    else
        git remote set-url "$REMOTE" "$NEW_URL"
        echo "Remote '$REMOTE' updated with token."
    fi
elif [ "$MODE" = "host" ]; then
    NEW_URL=$(strip_credentials "$URL")
    if [ "$URL" = "$NEW_URL" ]; then
        echo "Remote already clean (no credentials)."
    else
        git remote set-url "$REMOTE" "$NEW_URL"
        echo "Remote '$REMOTE' credentials stripped."
    fi
fi
