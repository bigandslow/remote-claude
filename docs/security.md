# Security

## Container Isolation

- Containers run as non-root user `claude`
- Limited sudo access: only package managers (apt-get, npm, pip) allowed
- Each session is isolated in its own container
- Workspace changes stay in the worktree for PR review

## Credential Protection

- All credentials mounted read-only
- GCP uses Workload Identity Federation (no long-lived keys)
- WIF tokens are short-lived and cleaned up after use
- Prefer environment variables for sensitive values

## Network Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| `bridge` | Normal Docker networking | Default, full internet access |
| `allowlist` | Proxy-based filtering | Production, restricted access |
| `none` | Complete isolation | Maximum security |

### Allowlist Mode

When `network.mode: allowlist`, a squid proxy filters traffic to only allowed domains:

```yaml
network:
  mode: allowlist
  allowed_domains:
    - github.com
    - pypi.org
    - registry.npmjs.org
    - api.anthropic.com
```

## Safety Hook

The safety hook (`hooks/safety.py`) blocks dangerous commands:

- `git push --force`, `git reset --hard`
- Database destructive operations (DROP, TRUNCATE)
- GCP resource deletion
- Recursive file deletion in system paths

### Audit Logs

Logs stored in `~/.config/remote-claude/audit/` with JSON format:

```json
{"ts": "2026-01-22T12:00:00", "session": "abc123", "decision": "block", "reason": "force push", "command": "git push --force"}
```

## Remote Access Security

- SSH key-based authentication recommended (disable password auth)
- Tailscale provides encrypted connections without port forwarding
- Responder requires `--allow-localhost` flag when Tailscale unavailable
- Rate limiting (10 requests/minute) on responder endpoint

## Webhook Security

- URLs validated to prevent SSRF attacks
- Private IP ranges blocked (10.x, 172.16-31.x, 192.168.x, 127.x)
- HTTPS required (warning on HTTP)
