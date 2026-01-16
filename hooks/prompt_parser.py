#!/usr/bin/env python3
"""
Prompt parser for Claude Code permission questions.

Detects and parses Claude Code permission prompts to extract:
- Tool name (Bash, Edit, Read, Write, etc.)
- Action description (command, file path, etc.)
- Available options (y/n/!/a/s)

Used by watch.py to send interactive notifications with appropriate context.

Usage:
  from prompt_parser import parse_permission_prompt, extract_prompt_from_output

  # Parse a known permission prompt
  result = parse_permission_prompt("Allow Bash to run: npm test [y/n/!]?")

  # Extract from tmux output
  prompt = extract_prompt_from_output(tmux_content)
  if prompt:
      result = parse_permission_prompt(prompt)
"""

import re
from dataclasses import dataclass
from typing import Optional, List, Tuple


@dataclass
class PermissionPrompt:
    """Parsed permission prompt."""
    tool: str  # Tool name (Bash, Edit, Write, etc.)
    action: str  # What the tool wants to do
    description: str  # Full human-readable description
    options: List[str]  # Available options (y, n, !, a, s)
    raw_prompt: str  # Original prompt text


# Patterns for different Claude Code permission prompts
# These match the actual Claude Code output format
PERMISSION_PATTERNS = [
    # "Allow Bash to run: `command`? [y/n/!]"
    # Note: Handle escaped ! (\!) from bash
    (
        r"Allow\s+(\w+)\s+(?:tool\s+)?to\s+run[:\s]+[`\"']?(.+?)[`\"']?\s*\??\s*\[([yn\\!as/]+)\]",
        lambda m: PermissionPrompt(
            tool=m.group(1),
            action=m.group(2).strip(),
            description=f"{m.group(1)} wants to run: {m.group(2).strip()}",
            options=list(m.group(3).replace("/", "").replace("\\", "")),
            raw_prompt=m.group(0),
        )
    ),
    # "Allow Edit to modify file.txt? [y/n/!]"
    (
        r"Allow\s+(\w+)\s+(?:tool\s+)?to\s+(modify|edit|change|update)\s+[`\"']?(.+?)[`\"']?\s*\??\s*\[([yn\\!as/]+)\]",
        lambda m: PermissionPrompt(
            tool=m.group(1),
            action=f"{m.group(2)} {m.group(3).strip()}",
            description=f"{m.group(1)} wants to {m.group(2)} {m.group(3).strip()}",
            options=list(m.group(4).replace("/", "").replace("\\", "")),
            raw_prompt=m.group(0),
        )
    ),
    # "Allow Write to create file.txt? [y/n/!]"
    (
        r"Allow\s+(\w+)\s+(?:tool\s+)?to\s+(create|write|overwrite)\s+[`\"']?(.+?)[`\"']?\s*\??\s*\[([yn\\!as/]+)\]",
        lambda m: PermissionPrompt(
            tool=m.group(1),
            action=f"{m.group(2)} {m.group(3).strip()}",
            description=f"{m.group(1)} wants to {m.group(2)} {m.group(3).strip()}",
            options=list(m.group(4).replace("/", "").replace("\\", "")),
            raw_prompt=m.group(0),
        )
    ),
    # "Allow Read to read file.txt? [y/n/!]"
    (
        r"Allow\s+(\w+)\s+(?:tool\s+)?to\s+(read|access|view)\s+[`\"']?(.+?)[`\"']?\s*\??\s*\[([yn\\!as/]+)\]",
        lambda m: PermissionPrompt(
            tool=m.group(1),
            action=f"{m.group(2)} {m.group(3).strip()}",
            description=f"{m.group(1)} wants to {m.group(2)} {m.group(3).strip()}",
            options=list(m.group(4).replace("/", "").replace("\\", "")),
            raw_prompt=m.group(0),
        )
    ),
    # Generic "Allow X to Y? [options]"
    (
        r"Allow\s+(\w+)\s+(?:tool\s+)?to\s+(.+?)\s*\??\s*\[([yn\\!as/]+)\]",
        lambda m: PermissionPrompt(
            tool=m.group(1),
            action=m.group(2).strip(),
            description=f"{m.group(1)} wants to {m.group(2).strip()}",
            options=list(m.group(3).replace("/", "").replace("\\", "")),
            raw_prompt=m.group(0),
        )
    ),
    # Permission request with tool description
    # "Bash: Run `npm test`? [y/n/!]"
    (
        r"^(\w+):\s+(?:Run|Execute|Create|Edit|Modify|Read|Write)\s+[`\"']?(.+?)[`\"']?\s*\??\s*\[([yn\\!as/]+)\]",
        lambda m: PermissionPrompt(
            tool=m.group(1),
            action=m.group(2).strip(),
            description=f"{m.group(1)}: {m.group(2).strip()}",
            options=list(m.group(3).replace("/", "").replace("\\", "")),
            raw_prompt=m.group(0),
        )
    ),
    # "Do you want to allow X? [y/n]"
    (
        r"Do you want to allow\s+(.+?)\s*\??\s*\[([yn\\!as/]+)\]",
        lambda m: PermissionPrompt(
            tool="Unknown",
            action=m.group(1).strip(),
            description=m.group(1).strip(),
            options=list(m.group(2).replace("/", "").replace("\\", "")),
            raw_prompt=m.group(0),
        )
    ),
    # Simple "[y/n/!]" at end of line (catch-all for permission prompts)
    (
        r"(.{10,100})\s*\[([yn\\!as/]+)\]\s*$",
        lambda m: PermissionPrompt(
            tool="Unknown",
            action=m.group(1).strip(),
            description=m.group(1).strip(),
            options=list(m.group(2).replace("/", "").replace("\\", "")),
            raw_prompt=m.group(0),
        )
    ),
]

# Patterns that indicate the prompt is asking for permission (not just any input)
# Note: Handle escaped characters (e.g., \! from bash)
PERMISSION_INDICATORS = [
    r"\[y/n\]",
    r"\[y/n/\\?!\]",  # Handle optional backslash escape
    r"\[y/n/!/a/s\]",
    r"\[yn\\?!\]",  # Handle optional backslash escape
    r"Allow\s+\w+\s+to",
    r"Do you want to allow",
    r"Permission required",
    r"Approve this action",
]


def parse_permission_prompt(text: str) -> Optional[PermissionPrompt]:
    """Parse a permission prompt and extract structured information.

    Args:
        text: The prompt text to parse

    Returns:
        PermissionPrompt if a valid permission prompt is found, None otherwise
    """
    # Clean up the text
    text = text.strip()
    if not text:
        return None

    # Try each pattern
    for pattern, builder in PERMISSION_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            try:
                return builder(match)
            except Exception:
                continue

    return None


def is_permission_prompt(text: str) -> bool:
    """Check if text contains a permission prompt.

    Args:
        text: Text to check

    Returns:
        True if the text appears to be asking for permission
    """
    for pattern in PERMISSION_INDICATORS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def extract_prompt_from_output(output: str, lines_to_check: int = 20) -> Optional[str]:
    """Extract a permission prompt from tmux output.

    Looks at the last N lines of output to find a permission prompt.

    Args:
        output: Full tmux pane content
        lines_to_check: Number of lines from the end to search

    Returns:
        The permission prompt line if found, None otherwise
    """
    if not output:
        return None

    lines = output.strip().split("\n")

    # Check last N lines
    search_lines = lines[-lines_to_check:] if len(lines) > lines_to_check else lines

    # Search from bottom up (most recent first)
    for line in reversed(search_lines):
        line = line.strip()
        if line and is_permission_prompt(line):
            return line

    # Also check combined last few lines (prompt might span multiple lines)
    combined = " ".join(search_lines[-5:])
    if is_permission_prompt(combined):
        # Try to extract just the prompt part
        for pattern in PERMISSION_INDICATORS:
            match = re.search(f".{{0,200}}{pattern}", combined, re.IGNORECASE)
            if match:
                return match.group(0)

    return None


def get_option_labels(options: List[str]) -> List[Tuple[str, str, str]]:
    """Get human-readable labels for permission options.

    Args:
        options: List of option characters (y, n, !, a, s)

    Returns:
        List of (char, label, description) tuples
    """
    option_info = {
        "y": ("y", "Yes", "Allow this action once"),
        "n": ("n", "No", "Deny this action"),
        "!": ("!", "Always", "Always allow this tool"),
        "a": ("a", "Abort", "Abort the current operation"),
        "s": ("s", "Skip", "Skip this action"),
    }

    result = []
    for opt in options:
        opt_lower = opt.lower()
        if opt_lower in option_info:
            result.append(option_info[opt_lower])

    return result


def format_notification_message(prompt: PermissionPrompt, max_length: int = 200) -> str:
    """Format a permission prompt for notification display.

    Args:
        prompt: Parsed permission prompt
        max_length: Maximum length of the message

    Returns:
        Formatted message string
    """
    # Truncate action if too long
    action = prompt.action
    if len(action) > max_length - 50:
        action = action[: max_length - 53] + "..."

    # Build message
    if prompt.tool and prompt.tool != "Unknown":
        msg = f"{prompt.tool}: {action}"
    else:
        msg = action

    return msg


# Example usage and testing
if __name__ == "__main__":
    # Test prompts
    test_prompts = [
        "Allow Bash to run: `npm test`? [y/n/!]",
        "Allow Edit to modify src/main.py? [y/n/!]",
        "Allow Write to create /tmp/test.txt? [y/n/!]",
        "Allow Read to read ~/.ssh/config? [y/n/!]",
        "Bash: Run `git status`? [y/n/!]",
        "Do you want to allow this file modification? [y/n]",
        "Allow Bash tool to run command: docker build -t test . [y/n/!]",
        "Permission required: Execute shell command? [y/n/!]",
    ]

    print("Permission Prompt Parser Test")
    print("=" * 50)

    for prompt_text in test_prompts:
        print(f"\nInput: {prompt_text}")
        result = parse_permission_prompt(prompt_text)
        if result:
            print(f"  Tool: {result.tool}")
            print(f"  Action: {result.action}")
            print(f"  Options: {result.options}")
            print(f"  Description: {result.description}")
        else:
            print("  (No match)")
