# Remote Claude Shell Integration
# Add this to your ~/.bashrc, ~/.zshrc, or ~/.profile
#
# Features:
# - rc command alias
# - rc-attach for quick session attachment
# - Optional auto-attach on SSH login

# Path to remote-claude installation
RC_HOME="${RC_HOME:-$HOME/GitHub/remote-claude}"

# Add rc.py to PATH (via alias)
alias rc="python3 $RC_HOME/rc.py"

# Quick attach function
rc-attach() {
    bash "$RC_HOME/setup/rc-attach.sh" "$@"
}

# Mosh helper: connect to remote host and attach to rc sessions
rc-mosh() {
    local host="$1"
    shift
    if [ -z "$host" ]; then
        echo "Usage: rc-mosh <host> [mosh-options]"
        echo "  Connects via mosh and runs rc-attach"
        return 1
    fi
    mosh "$@" "$host" -- bash -l -c 'rc-attach --auto || bash -l'
}

# SSH helper: connect to remote host and attach to rc sessions
rc-ssh() {
    local host="$1"
    shift
    if [ -z "$host" ]; then
        echo "Usage: rc-ssh <host> [ssh-options]"
        echo "  Connects via ssh and runs rc-attach"
        return 1
    fi
    ssh "$@" -t "$host" 'bash -l -c "rc-attach --auto || bash -l"'
}

# ============================================================
# Auto-attach on SSH login (optional)
# Uncomment the following to auto-attach when SSH'ing in
# ============================================================

# auto_attach_on_login() {
#     # Only if:
#     # - This is an SSH session
#     # - Not already in tmux
#     # - Not running a specific command
#     if [ -n "$SSH_CONNECTION" ] && [ -z "$TMUX" ] && [ -z "$RC_NO_AUTO_ATTACH" ]; then
#         # Check if there are any rc sessions
#         if tmux -L remote-claude list-sessions &>/dev/null; then
#             echo "Remote Claude sessions available. Attaching..."
#             sleep 1
#             rc-attach --auto
#         fi
#     fi
# }
#
# # Call on shell startup
# auto_attach_on_login

# ============================================================
# Prompt integration (optional)
# Shows active rc session count in prompt
# ============================================================

# For bash, add to PS1:
# rc_session_count() {
#     local count=$(tmux -L remote-claude list-sessions 2>/dev/null | grep -c "^rc-" || echo 0)
#     if [ "$count" -gt 0 ]; then
#         echo "[rc:$count]"
#     fi
# }
# PS1='$(rc_session_count) '$PS1

# For zsh:
# rc_session_count() {
#     local count=$(tmux -L remote-claude list-sessions 2>/dev/null | grep -c "^rc-" || echo 0)
#     if [ "$count" -gt 0 ]; then
#         echo "[rc:$count]"
#     fi
# }
# PROMPT='$(rc_session_count) '$PROMPT
