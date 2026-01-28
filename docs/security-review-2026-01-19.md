# Security Review: Remote Claude

**Date:** 2026-01-19
**Reviewer:** Security Review Agent
**Scope:** Comprehensive security analysis of remote-claude codebase

---

## Executive Summary

Remote Claude is a Docker-based Claude Code session manager with tmux persistence and push notification support. The project demonstrates strong security awareness with good foundational practices, but has **two critical gaps** that should be addressed before production use:

1. Network isolation (allowlist mode) is not implemented
2. Audit logging is insufficient (ephemeral /tmp storage)

**Overall Risk Level:**
- Development/Testing: **LOW** (with Tailscale VPN)
- Production: **MODERATE** (requires network isolation and audit logging)

---

## Findings by Severity

### CRITICAL

#### 1. Network Allowlist Not Implemented

**Location:** `lib/docker_manager.py:268`
**Status:** TODO comment - feature incomplete

**Issue:** The `network_mode: allowlist` configuration option is defined but not implemented. Containers can access any domain regardless of the allowlist setting.

**Impact:** Containers have unrestricted network access, defeating the purpose of domain isolation. A compromised agent could exfiltrate data to arbitrary endpoints.

**Recommendation:** Implement allowlist using one of:
- iptables rules in container
- Transparent proxy (squid/mitmproxy)
- Docker network with DNS filtering

---

#### 2. Temporary WIF Credential Files Not Securely Cleaned

**Location:** `lib/docker_manager.py:232-253`

**Issue:** WIF token files are created with `tempfile.NamedTemporaryFile(delete=False)` and mounted into containers. The files are not explicitly deleted after container startup.

**Impact:** GCP identity tokens remain on disk in `/tmp/` after use. If host is compromised, tokens could be harvested (though short-lived).

**Recommendation:**
```python
import atexit

def _cleanup_temp_files():
    for f in temp_files_to_cleanup:
        try:
            os.unlink(f)
        except OSError:
            pass

atexit.register(_cleanup_temp_files)
```

---

### HIGH

#### 3. No Persistent Audit Logging

**Location:** `hooks/safety.py:151-161`

**Issue:** Safety hook decisions are logged to `/tmp/rc-safety-logs/` which is cleared on system reboot. No permanent audit trail exists for blocked or escalated commands.

**Impact:** Cannot investigate security incidents or demonstrate compliance. No record of what commands were blocked/escalated.

**Recommendation:**
- Log to `~/.config/remote-claude/audit.log`
- Include: timestamp, session_id, command, decision, reason
- Implement log rotation (e.g., 10MB max, 5 files)

---

#### 4. Pushover Credentials in Config File

**Location:** `hooks/notify.py:344-345`, `config/config.yaml`

**Issue:** Pushover API token and user key can be stored in plaintext config file.

**Impact:** Credentials readable by any process running as user. Config file may be accidentally committed to git.

**Recommendation:**
- Prefer environment variables (`PUSHOVER_API_TOKEN`, `PUSHOVER_USER_KEY`)
- Add `config.yaml` to `.gitignore` (already done)
- Document credential security in README

---

#### 5. No Rate Limiting on Responder Endpoint

**Location:** `hooks/responder.py:289-311`

**Issue:** The `/respond` endpoint has no rate limiting. While tokens are single-use and time-limited, the endpoint could be targeted for DoS or brute-force attacks.

**Impact:** Potential denial of service; theoretical token brute-forcing (mitigated by HMAC and short expiry).

**Recommendation:**
```python
from collections import defaultdict
import time

request_counts = defaultdict(list)
RATE_LIMIT = 10  # requests per minute

def check_rate_limit(client_ip):
    now = time.time()
    request_counts[client_ip] = [t for t in request_counts[client_ip] if now - t < 60]
    if len(request_counts[client_ip]) >= RATE_LIMIT:
        return False
    request_counts[client_ip].append(now)
    return True
```

---

### MODERATE

#### 6. NOPASSWD Sudo in Container

**Location:** `docker/Dockerfile:41`

**Issue:** The `claude` user has passwordless sudo access inside the container.

**Impact:** If the Claude agent is compromised or tricked into running malicious commands, it can escalate to root within the container. Container isolation limits blast radius to container only.

**Recommendation:**
- Document this risk in README
- Consider removing sudo or requiring password for production
- Alternative: use capabilities instead of full sudo

---

#### 7. Localhost Fallback When Tailscale Unavailable

**Location:** `hooks/responder.py:420-436`

**Issue:** If Tailscale IP cannot be detected, responder falls back to binding on localhost.

**Impact:** Less secure default - localhost binding may be accessible to other processes on the same machine.

**Recommendation:**
- Log warning when falling back to localhost
- Consider requiring explicit `--allow-localhost` flag
- Document security implications

---

#### 8. Webhook URLs Not Validated

**Location:** `hooks/notify.py:477`

**Issue:** Custom webhook URLs from config are used without validation.

**Impact:** If config file is modified by attacker, could enable Server-Side Request Forgery (SSRF) to internal services.

**Recommendation:**
- Validate URLs against allowlist of safe domains
- Reject private IP ranges (10.x, 192.168.x, 172.16-31.x)
- Warn on non-HTTPS URLs

---

#### 9. No Version Pinning in Dockerfile

**Location:** `docker/Dockerfile`

**Issue:** Base image and packages use `latest` or unpinned versions:
- `FROM ubuntu:22.04` (not pinned to digest)
- `@anthropic-ai/claude-code@latest`

**Impact:** Builds are not reproducible; supply chain risk from compromised upstream packages.

**Recommendation:**
```dockerfile
FROM ubuntu:22.04@sha256:xxxx
RUN npm install -g @anthropic-ai/claude-code@1.0.5
```

---

### LOW / INFORMATIONAL

#### 10. Session ID Generation From Timestamp

**Location:** `lib/docker_manager.py`

**Issue:** Session IDs are generated from timestamp (predictable pattern).

**Impact:** Low risk due to short token expiry and local scope, but could be improved.

**Recommendation:** Use `secrets.token_hex(8)` for session IDs.

---

#### 11. Environment Variables Could Leak Secrets

**Issue:** Initial prompt passed via `-p RC_PROMPT` argument, visible in process list.

**Impact:** Other users on shared system could see prompt contents.

**Recommendation:** Use stdin or config file for sensitive prompts.

---

#### 12. No SBOM or Dependency Scanning

**Issue:** No Software Bill of Materials or automated vulnerability scanning configured.

**Recommendation:** Add GitHub Dependabot or similar for dependency monitoring.

---

## Positive Security Findings

### Strong Practices Observed

1. **Principle of Least Privilege**
   - Non-root user in container (`claude` user)
   - Limited GCP service account roles
   - Read-only credential mounts

2. **Defense in Depth**
   - Safety hook as primary command filter
   - HMAC token signing for responder
   - Tailscale binding by default

3. **Secure Defaults**
   - Single-use tokens with 5-minute expiry
   - Tailscale IP binding (not 0.0.0.0)
   - Read-only mounts for credentials

4. **No Dangerous Patterns**
   - No `eval()` or `exec()` usage
   - No pickle deserialization
   - No hardcoded secrets
   - Uses `yaml.safe_load()` consistently

5. **Comprehensive Safety Hook**
   - Blocks dangerous git, filesystem, database commands
   - Escalates infrastructure changes to user
   - Self-protection (blocks modifications to safety files)
   - Good test coverage

6. **GCP Best Practices**
   - Workload Identity Federation (no long-lived keys)
   - Scoped service account roles
   - Dynamic token generation

---

## Architecture Security Assessment

### Authentication & Authorization

| Component | Method | Assessment |
|-----------|--------|------------|
| Container access | Non-root user | ✅ Good |
| Credential mounting | Read-only bind mounts | ✅ Good |
| GCP auth | WIF with scoped roles | ✅ Strong |
| Responder tokens | HMAC-SHA256, 5-min expiry | ✅ Strong |
| Safety hook | Pattern matching | ✅ Good (with caveats) |

### Data Flow Security

| Flow | Protection | Assessment |
|------|------------|------------|
| User → Container | Workspace isolation | ✅ Good |
| Container → APIs | Network isolation | ❌ Not implemented |
| Container → GCP | WIF tokens | ✅ Strong |
| Safety decisions | Audit logging | ⚠️ Ephemeral only |
| Notifications | HTTPS webhooks | ✅ Good |

---

## Recommendations Summary

### Immediate (Before Production Use)

1. ☐ Implement network allowlist mode
2. ☐ Add secure cleanup for temp credential files
3. ☐ Implement persistent audit logging
4. ☐ Add rate limiting to responder endpoint

### Short-Term (Next Release)

5. ☐ Move notification credentials to env vars only
6. ☐ Validate webhook URLs against allowlist
7. ☐ Pin Docker image versions
8. ☐ Document NOPASSWD sudo risks

### Medium-Term (Roadmap)

9. ☐ Add integration tests for Docker operations
10. ☐ Implement audit log rotation
11. ☐ Add SBOM generation
12. ☐ Improve session ID randomness

---

## Testing Gaps Identified

| Component | Test Coverage | Recommendation |
|-----------|---------------|----------------|
| `hooks/safety.py` | ✅ Comprehensive | Maintain |
| `hooks/responder.py` | ❌ None | Add endpoint tests |
| `lib/docker_manager.py` | ❌ None | Add integration tests |
| `lib/config.py` | ❌ None | Add validation tests |
| Network isolation | ❌ None | Add connectivity tests |

---

## Compliance Notes

- **Data Retention:** No persistent user data storage (session-scoped)
- **Multi-Tenancy:** Not designed for multi-tenant use (single user assumed)
- **GCP Compliance:** WIF approach aligns with best practices
- **Audit Trail:** Requires implementation before compliance use

---

## Conclusion

Remote Claude demonstrates excellent security awareness and follows many best practices. The codebase is well-structured with clear separation of concerns. The safety hook provides strong protection against common dangerous operations.

**Key gaps to address:**
1. Network isolation must be implemented (currently TODO)
2. Audit logging needs to be persistent
3. Rate limiting should be added to the responder

With these improvements, the project would be suitable for production use in security-conscious environments.

---

## Appendix: Files Reviewed

- `docker/Dockerfile` - Container security configuration
- `lib/docker_manager.py` - Container lifecycle and credential handling
- `lib/config.py` - Configuration parsing and validation
- `hooks/safety.py` - Command filtering and validation
- `hooks/responder.py` - Interactive notification endpoint
- `hooks/notify.py` - Push notification delivery
- `infra/__main__.py` - GCP infrastructure and WIF setup
- `config/config.yaml.example` - Configuration template
- `tests/test_safety.py` - Safety hook test coverage
