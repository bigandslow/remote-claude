#!/usr/bin/env python3
"""
PreToolUse hook to block or escalate destructive commands.

This hook intercepts Bash commands before execution and:
- Blocks clearly destructive commands (returns JSON with decision: "block")
- Escalates ambiguous commands to user (exit code 2)
- Allows safe commands (exit 0 with no output)

Install by adding to ~/.claude/settings.json:
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": ["python3 ~/GitHub/remote-claude/hooks/safety.py"]
      }
    ]
  }
}
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

# Load config if available - check multiple locations
CONFIG_PATHS = [
    Path(__file__).parent / "safety_config.yaml",  # Same directory as script
    Path("/home/claude/.rc-hooks/safety_config.yaml"),  # Container mount
]
CONFIG_PATH = next((p for p in CONFIG_PATHS if p.exists()), CONFIG_PATHS[0])


# Patterns that should be BLOCKED (clearly destructive)
BLOCKED_PATTERNS = {
    "git_force": [
        (r"git\s+push\s+.*--force", "Force push can overwrite remote history"),
        (r"git\s+push\s+-f\s", "Force push can overwrite remote history"),
        (r"git\s+push\s+--force-with-lease", "Force push can overwrite remote history"),
    ],
    "git_destructive": [
        (r"git\s+reset\s+--hard", "Hard reset discards uncommitted changes"),
        (r"git\s+clean\s+-fd", "Clean -fd deletes untracked files"),
        (r"git\s+clean\s+-[a-z]*f[a-z]*d", "Clean with -f and -d deletes files"),
        (r"git\s+branch\s+-[dD]\s+(main|master)\b", "Deleting main/master branch"),
    ],
    "filesystem": [
        (r"rm\s+-rf\s+/(?!\w)", "Deleting root filesystem"),
        (r"rm\s+-rf\s+~", "Deleting home directory"),
        (r"rm\s+-rf\s+\.\s*$", "Deleting current directory recursively"),
        (r"rm\s+-rf\s+\*", "Deleting all files recursively"),
        (r"chmod\s+777\s+/", "Setting world-writable permissions on root"),
    ],
    "safety_bypass": [
        # Prevent modifying Claude hooks configuration
        (r"\.claude/settings\.json", "Modifying Claude settings/hooks"),
        (r"\.claude/settings\.local\.json", "Modifying Claude local settings"),
        # Prevent modifying git hooks
        (r"\.git/hooks/pre-commit", "Modifying git pre-commit hook"),
        (r"\.git/hooks/pre-push", "Modifying git pre-push hook"),
        (r"\.git/hooks/commit-msg", "Modifying git commit-msg hook"),
        # Prevent modifying the safety hook itself
        (r"hooks/safety\.py", "Modifying safety hook"),
        (r"hooks/safety_config\.yaml", "Modifying safety config"),
    ],
    "database_destructive": [
        (r"DROP\s+DATABASE", "Dropping database"),
        (r"DROP\s+SCHEMA", "Dropping schema"),
        (r"TRUNCATE\s+TABLE", "Truncating table"),
    ],
    "gcp_destructive": [
        (r"gcloud\s+projects\s+delete", "Deleting GCP project"),
        (r"gcloud\s+iam\s+.*remove-iam-policy-binding", "Removing IAM bindings"),
        (r"gcloud\s+compute\s+instances\s+delete", "Deleting compute instances"),
        (r"gcloud\s+sql\s+instances\s+delete", "Deleting SQL instances"),
        (r"gcloud\s+storage\s+.*rm\s+-r", "Recursively deleting storage"),
    ],
}

# Patterns that should be ESCALATED (ask user)
ESCALATE_PATTERNS = {
    "git_push_protected": [
        (r"git\s+push\s+.*\b(main|master)\b", "Pushing to protected branch"),
        (r"git\s+push\s+origin\s+(main|master)", "Pushing to main/master"),
    ],
    "pulumi": [
        (r"^pulumi\s+", "Pulumi infrastructure command"),
        (r"\bpulumi\s+(up|destroy|preview)", "Pulumi state-changing command"),
    ],
    "terraform": [
        (r"^terraform\s+(apply|destroy)", "Terraform state-changing command"),
    ],
    "database_migrations": [
        (r"(alembic|flask\s+db)\s+(upgrade|downgrade)", "Database migration"),
        (r"prisma\s+(migrate|db\s+push)", "Prisma migration"),
    ],
    "kubernetes": [
        (r"kubectl\s+delete", "Kubernetes delete command"),
        (r"kubectl\s+apply.*--force", "Kubernetes force apply"),
    ],
    "gcp_quiet": [
        (r"gcloud\s+.*--quiet", "GCP command with --quiet flag"),
    ],
}


def load_custom_config() -> Tuple[dict, dict]:
    """Load custom patterns from config file if it exists."""
    if not CONFIG_PATH.exists():
        return BLOCKED_PATTERNS, ESCALATE_PATTERNS

    try:
        import yaml
        with open(CONFIG_PATH) as f:
            config = yaml.safe_load(f)

        blocked = BLOCKED_PATTERNS.copy()
        escalate = ESCALATE_PATTERNS.copy()

        if config and "blocked" in config:
            for category, patterns in config["blocked"].items():
                blocked[category] = [(p["pattern"], p["reason"]) for p in patterns]

        if config and "escalate" in config:
            for category, patterns in config["escalate"].items():
                escalate[category] = [(p["pattern"], p["reason"]) for p in patterns]

        return blocked, escalate
    except Exception:
        return BLOCKED_PATTERNS, ESCALATE_PATTERNS


def check_command(command: str, patterns: dict) -> Optional[Tuple[str, str]]:
    """Check if command matches any patterns.

    Returns:
        Tuple of (category, reason) if matched, None otherwise
    """
    for category, pattern_list in patterns.items():
        for pattern, reason in pattern_list:
            if re.search(pattern, command, re.IGNORECASE | re.MULTILINE):
                return (category, reason)
    return None


def log_decision(command: str, decision: str, reason: str):
    """Log blocked/escalated commands for audit."""
    log_dir = Path("/tmp/rc-safety-logs")
    log_dir.mkdir(exist_ok=True)

    log_file = log_dir / f"{datetime.now().strftime('%Y-%m-%d')}.log"
    timestamp = datetime.now().isoformat()

    with open(log_file, "a") as f:
        f.write(f"{timestamp} | {decision.upper()} | {reason} | {command[:100]}\n")


def main():
    """Main entry point for PreToolUse hook."""
    # Read hook input from stdin
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError:
        # Invalid input, allow command to proceed
        sys.exit(0)

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})

    # Only check Bash commands
    if tool_name != "Bash":
        sys.exit(0)

    command = tool_input.get("command", "")
    if not command:
        sys.exit(0)

    # Load patterns (with custom config if available)
    blocked_patterns, escalate_patterns = load_custom_config()

    # Check for blocked patterns first
    blocked = check_command(command, blocked_patterns)
    if blocked:
        category, reason = blocked
        log_decision(command, "blocked", reason)
        result = {
            "decision": "block",
            "reason": f"[SAFETY] {reason}. Category: {category}",
        }
        print(json.dumps(result))
        sys.exit(0)

    # Check for escalate patterns
    escalate = check_command(command, escalate_patterns)
    if escalate:
        category, reason = escalate
        log_decision(command, "escalate", reason)
        # Exit code 2 tells Claude to ask the user
        sys.exit(2)

    # Command is allowed
    sys.exit(0)


if __name__ == "__main__":
    main()
