# tmux Quick Reference

All commands use prefix `Ctrl-b` then the key.

## Sessions
| Action | Command |
|--------|---------|
| Detach | `Ctrl-b d` |
| List | `tmux ls` |
| Attach | `tmux attach -t name` |
| Kill | `tmux kill-session -t name` |

## Windows
| Action | Command |
|--------|---------|
| New | `Ctrl-b c` |
| Next/Prev | `Ctrl-b n` / `Ctrl-b p` |
| Select by # | `Ctrl-b 0-9` |
| Rename | `Ctrl-b ,` |
| Close | `exit` or `Ctrl-d` |

## Panes
| Action | Command |
|--------|---------|
| Split horizontal | `Ctrl-b "` |
| Split vertical | `Ctrl-b %` |
| Navigate | `Ctrl-b arrow` |
| Close | `Ctrl-d` |

## Copy Mode (scrollback)
| Action | Command |
|--------|---------|
| Enter | `Ctrl-b [` |
| Exit | `q` |
| Scroll | Arrow keys or PgUp/PgDn |

## Pasting Images in Claude Code

tmux doesn't support clipboard images. Use file path instead:

1. Save screenshot: `Cmd-Shift-4` (saves to Desktop)
2. In Claude, type the path: `/Users/you/Desktop/Screenshot....png`
3. Or drag file into terminal (pastes path)

For remote sessions, copy image to server first:
```bash
scp ~/Desktop/screenshot.png server:/tmp/
# Then in Claude: /tmp/screenshot.png
```
