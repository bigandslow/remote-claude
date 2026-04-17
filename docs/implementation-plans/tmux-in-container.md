# Move tmux Into Container

## Problem

remote-claude requires tmux on the host machine. This blocks Windows support
(tmux is unavailable on Windows, even with Git Bash) and adds a dependency
that complicates onboarding on all platforms.

## Solution

Run tmux inside the Docker container instead of on the host. The container's
entrypoint starts tmux as its main process, and all session interaction happens
via `docker exec`. The host no longer needs tmux installed.

This also enables iTerm2's `-CC` (control mode) integration, giving users
native scrollback, search, and split panes instead of tmux's built-in
Ctrl+b [ scrollback.

## Architecture Change

### Current flow

```
Host                          Container
─────                         ─────────
tmux session ──> docker attach ──> entrypoint.sh ──> claude (while true loop)
```

- `rc start`: creates container + host tmux session wrapping `docker attach`
- `rc attach`: `os.execvp("tmux", ["tmux", "attach-session", ...])`
- `rc send`: `tmux send-keys` on host
- `rc list`: queries host `tmux list-sessions` + Docker containers

### New flow

```
Host                          Container
─────                         ─────────
docker exec -it ──────────────> tmux session ──> claude (while true loop)
```

- `rc start`: creates container (entrypoint starts tmux + claude inside it)
- `rc attach`: `os.execvp("docker", ["docker", "exec", "-it", container, "tmux", "attach", ...])`
- `rc send`: `docker exec container tmux send-keys ...`
- `rc list`: queries Docker containers only

## Implementation Plan

### Phase 1: Container changes (foundation)

**Dockerfile** — add tmux to apt-get install (1 line).

**entrypoint.sh** — refactor the ending:
- Accept `RC_TMUX_SOCKET` and `RC_TMUX_SESSION` env vars
- Extract the Claude restart loop into `/tmp/claude-loop.sh`
- Replace `exec claude` with `exec tmux -L $socket new-session -s $session /tmp/claude-loop.sh`
- Setup mode (`RC_SETUP_MODE=1`) also starts tmux
- All existing setup (SSH config fix, deploy keys, plugins, etc.) runs before tmux starts

Testable independently: `docker run -it remote-claude:latest` should start tmux
with claude inside it. `docker exec ... tmux list-sessions` should show the session.

### Phase 2: ContainerTmuxManager (new abstraction)

**lib/container_tmux_manager.py** — new class wrapping tmux via docker exec:

```python
class ContainerTmuxManager:
    def __init__(self, container_name: str, socket_name: str = "remote-claude"):
        self.container = container_name
        self.socket = socket_name

    def session_exists(self, session_name: str) -> bool:
        # docker exec container tmux -L socket has-session -t session

    def send_keys(self, session_name: str, keys: str, enter: bool = True):
        # docker exec container tmux -L socket send-keys -t session ...

    def capture_pane(self, session_name: str, lines: int = 50) -> str:
        # docker exec container tmux -L socket capture-pane -t session -p -S -lines

    def attach_session(self, session_name: str):
        # os.execvp("docker", ["docker", "exec", "-it", container, "tmux", ...])

    def kill_session(self, session_name: str):
        # docker exec container tmux -L socket kill-session -t session
```

Interface matches TmuxManager so `_wait_for_pattern()` and other polymorphic
callers work unchanged.

**lib/cloud_container_tmux_manager.py** — same but routes through SSH:
- `ssh host docker exec container tmux -L socket <args>`
- `attach_session()` uses `os.execvp("ssh", ["ssh", "-t", ..., "docker", "exec", "-it", ...])`

### Phase 3: rc.py refactor

Each command flow changes from host TmuxManager to ContainerTmuxManager.

**`__init__()`** — remove `self.tmux = TmuxManager(...)`. Store config values
(socket_name, prefix) for passing to containers and managers.

**`start()`**:
- Pass `RC_TMUX_SOCKET` and `RC_TMUX_SESSION` as container env vars
- Remove host tmux session creation
- Add readiness poll: wait for `docker exec container tmux has-session` to succeed
- Create `ContainerTmuxManager(container_name)` for `_auto_select_theme()`
- Attach via `ContainerTmuxManager.attach_session()`
- Support `--tmux-cc` flag for iTerm2 integration mode

**`attach()`** — replace host tmux attach with `ContainerTmuxManager.attach_session()`.
Support `--tmux-cc` flag.

**`send()`** — replace host `send_keys` / `capture_pane` with ContainerTmuxManager.
`_wait_for_pattern()` already accepts a tmux-like object; just pass the new manager.

**`list_sessions()`** — simplify to Docker container listing only. Drop the
tmux session correlation logic. Optionally check attached status via
`docker exec container tmux list-clients`.

**`kill()`** — remove `self.tmux.kill_session()`. Just `docker stop` + `docker rm`.
Killing the container kills its tmux.

**`restart()`** — use ContainerTmuxManager to send `/exit` via docker exec.

**`setup --headless`** — setup container also starts tmux inside it. Create
ContainerTmuxManager for the setup container. Poll/send via docker exec.

**`switch()`** — remove host tmux kill/create. New container gets tmux via entrypoint.

**Cloud flows** — same changes, using CloudContainerTmuxManager instead of
CloudTmuxManager. `_start_cloud()`, `_attach_cloud()`, `_send_cloud()`,
`_kill_cloud()` all route through the new manager.

### Phase 4: Hooks

**hooks/watch.py**:
- Discover sessions via `docker ps --filter name=rc-` instead of `tmux list-sessions`
- Capture panes via `docker exec container tmux capture-pane`
- Needs container_name for each session (available from session registry)

**hooks/responder.py**:
- `send_tmux_keys()` → `docker exec container tmux send-keys`
- `session_exists()` → `docker inspect` or `docker exec ... tmux has-session`
- Map session_name to container_name via session registry

**hooks/notify.py**:
- Update Blink Shell deep link commands to use docker exec pattern

### Phase 5: iTerm2 integration mode

Add `--tmux-cc` flag to `rc attach` and `rc start`:

```bash
rc attach myproject --tmux-cc
```

Implementation: attach with `tmux -CC attach-session` instead of `tmux attach-session`.
iTerm2 detects `-CC` and translates tmux windows into native iTerm2 tabs with
native scrollback, search, and split panes.

This flag is optional. Without it, standard tmux attach works in any terminal.

### Phase 6: Cleanup

- Remove `lib/tmux_manager.py` (or keep as deprecated reference)
- Remove `lib/cloud_tmux_manager.py`
- Rewrite `setup/rc-attach.sh` for docker exec
- Update all docs to remove host tmux as prerequisite
- Update `docs/windows-wsl2-setup.md` — remove `sudo apt install tmux`
- Add Git Bash to supported Windows environments in docs

## docker logs regression

With tmux inside the container, Claude's output goes to tmux panes instead of
container stdout. `docker logs` / `rc logs` will show less.

Mitigation options:
1. `tmux pipe-pane` to tee output to a log file; `rc logs` reads it via docker exec
2. Accept that `rc logs` shows only entrypoint startup; users use `rc send ... capture`
3. Write tmux output to `/tmp/claude.log` and tail it for `rc logs`

Recommend option 1: add `tmux pipe-pane -t $session 'cat >> /tmp/claude.log'`
after session creation. `rc logs` becomes `docker exec container cat /tmp/claude.log`
or `docker exec container tail -f /tmp/claude.log` for follow mode.

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| `docker exec` overhead for polling | Low | ~50-100ms per call, polling at 1s intervals |
| Container stop kills tmux sessions | Expected | Same as today (host tmux session referencing dead container) |
| `docker logs` shows less output | Certain | pipe-pane to log file |
| Setup headless flow complexity | Medium | Needs careful testing; tmux must start in setup mode too |
| Hooks break (watch.py, responder.py) | Medium | Session registry provides container name mapping |

## Scope

| Area | Effort |
|------|--------|
| Dockerfile + entrypoint.sh | 1-2 hours |
| ContainerTmuxManager + cloud variant | 3-4 hours |
| rc.py refactor (all command flows) | 4-6 hours |
| Hooks (watch.py, responder.py, notify.py) | 2-3 hours |
| iTerm2 --tmux-cc support | 1 hour |
| Setup scripts + docs | 2-3 hours |
| Testing | 3-4 hours |
| **Total** | **~16-24 hours** |

## Breaking Changes

- Host tmux no longer required (existing users unaffected, just unnecessary)
- `rc logs` output changes (less verbose by default)
- Hooks (watch.py, responder.py) need updating — users running custom notification
  setups will need to update
- `setup/rc-attach.sh` rewritten — users who source it need the new version
- Config `tmux:` section unchanged (socket_name and prefix now configure in-container tmux)
