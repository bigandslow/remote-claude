# Multi-Account Support for remote-claude

## Overview

Add support for multiple Claude accounts to handle usage limits and different contexts.

## Design Principles

1. **Simple and robust** - no fragile hacks for session persistence
2. **Workspace preserved** - code files survive account switches
3. **Fresh conversation** - switching accounts starts a new Claude conversation
4. **Setup wizard** - guided account setup with `rc account add`
5. **Configurable rate limit handling** - manual, notify, or auto-rotate

## Config Structure

The existing `credentials` section becomes the implicit `default` profile. Account profiles can override any credential path, falling back to the global `credentials` values.

```yaml
credentials:
  # These serve as the 'default' profile (backward compatible)
  anthropic: ~/.anthropic
  claude: ~/.claude
  git: ~/.gitconfig
  ssh: ~/.ssh
  claude_gcp: ~/.config/gcloud/application_default_credentials.json

accounts:
  default: personal  # Which profile to use when --account not specified
  on_rate_limit: notify  # "manual", "notify", or "auto"
  profiles:
    personal:
      # Uses all credentials from the global 'credentials' section
      # (no overrides needed for personal account)
    work:
      # Override only what differs for this account
      anthropic: ~/.anthropic-work
      claude: ~/.claude-work
      # Optional: separate git identity for work context
      git: ~/.gitconfig-work
      # Optional: different GCP project
      claude_gcp: ~/.config/gcloud/work-credentials.json
```

## Commands

### Start with account
```bash
rc start ~/project --account work
```

### Manage accounts
```bash
rc account list                 # List configured accounts
rc account add <name>           # Setup wizard for new account
rc account remove <name>        # Remove account profile
```

### Switch account
```bash
rc switch <session> <account>   # Switch session to different account
```

### Status
```bash
rc status [session]             # Show session info including current account
```

## Switch Behavior

When switching accounts:
1. Stop current container
2. Start new container with new account's credentials
3. Recreate tmux session attached to new container
4. **Workspace code preserved** (same bind mount)
5. **Claude conversation starts fresh** (accepted limitation)

## Rate Limit Handling

Configured via `accounts.on_rate_limit`:

| Mode | Behavior |
|------|----------|
| `manual` | User runs `rc switch` when needed |
| `notify` | Send push notification suggesting switch when rate limited |
| `auto` | Automatically switch to next available account |

Detection strategy (both patterns and exit codes):
- **Patterns**: Watch container stdout/stderr for: `rate limit`, `too many requests`, `429`, `quota exceeded`
- **Exit codes**: Monitor Claude process exit codes indicating API errors
- **Debounce**: Avoid false positives from transient errors; require sustained pattern before triggering

## Setup Wizard Flow

`rc account add work`:
1. Create directories: `~/.anthropic-work/`, `~/.claude-work/`
2. Launch temporary container with those mounts
3. Run `claude /login` inside container to authenticate
4. Verify credentials work (test API call)
5. Add profile to config.yaml
6. Optionally prompt for git/ssh credential paths if user wants separate identity

## Implementation

### Phase 1: Core Multi-Account
1. **lib/config.py**: Add `AccountsConfig` dataclass with profiles
2. **lib/docker_manager.py**: Accept `account` param, resolve credentials from profile
3. **rc.py**: Add `--account` flag to `start`, add `account` and `switch` commands
4. **Container labels**: Store account name as label for status display

### Phase 2: Setup Wizard
1. **rc.py**: Implement `account add` wizard
2. Create credential directories
3. Launch temp container for `/login`
4. Update config.yaml

### Phase 3: Rate Limit Handling
1. **hooks/watch.py**: Detect rate limit patterns in session output
2. **hooks/notify.py**: Add rate limit notification type
3. **rc.py**: Implement auto-rotation logic

## Files to Modify

| File | Changes |
|------|---------|
| `lib/config.py` | `AccountsConfig`, `AccountProfile` dataclasses |
| `lib/docker_manager.py` | `account` param, credential resolution, account label |
| `rc.py` | `--account` flag, `account` subcommand, `switch` command |
| `docs/configuration.md` | Document account profiles |

## Container Labels

Add label to track account:
```python
f"--label rc.account={account_name}"
```

Display in `rc list` and `rc status`.
