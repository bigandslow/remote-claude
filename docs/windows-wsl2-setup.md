# Windows Setup (WSL2)

Remote Claude runs on Windows via **WSL2** (Windows Subsystem for Linux). Docker Desktop for Windows with WSL2 integration provides a fully compatible Linux environment.

## Prerequisites

1. **Windows 10 version 2004+ or Windows 11**
2. **WSL2** with Ubuntu 22.04 or 24.04
3. **Docker Desktop for Windows** with WSL2 integration enabled
4. **Python 3.10+** (inside WSL2)

## Installation

### 1. Install WSL2

Open PowerShell as Administrator:

```powershell
wsl --install
```

This installs WSL2 with Ubuntu by default. Restart when prompted.

### 2. Install Docker Desktop

Download and install [Docker Desktop for Windows](https://docs.docker.com/desktop/install/windows-install/).

After installation, enable WSL2 integration:

- Docker Desktop → Settings → Resources → WSL Integration
- Enable "Use the WSL 2 based engine"
- Enable your Ubuntu distribution

### 3. Install wslu (optional, for browser auto-open)

`wslview` from the `wslu` package opens URLs in your default Windows browser. Without it, OAuth URLs are printed to the terminal instead.

```bash
sudo apt install wslu
```

### 4. Set up remote-claude

Inside your WSL2 terminal, follow the standard setup:

```bash
# Clone the repo
git clone git@github.com:your-org/remote-claude.git ~/remote-claude

# Install Python dependencies
pip3 install -r requirements.txt

# Build the Docker image (requires Docker Desktop running)
python3 rc.py build

# Run headless setup
python3 rc.py setup --headless
```

### 5. Add rc to PATH

```bash
echo 'alias rc="python3 ~/remote-claude/rc.py"' >> ~/.bashrc
source ~/.bashrc
```

## Usage

All `rc` commands work the same as on macOS/Linux:

```bash
# Start a session
rc start ~/projects/myapp

# List sessions
rc list

# Attach to a session
rc attach <session-id>
```

## Notes

### Workspace paths

If your project is on a Windows drive (e.g., `C:\Users\you\projects\myapp`), it appears in WSL2 at `/mnt/c/Users/you/projects/myapp`. Docker Desktop handles these paths automatically in volume mounts.

```bash
rc start /mnt/c/Users/you/projects/myapp
```

For better performance, keep project files in the WSL2 filesystem (e.g., `~/projects/`) rather than on Windows drives.

### Docker Desktop must be started from Windows

If Docker is not running, rc will print:

```
Please start Docker Desktop from the Windows taskbar and try again.
```

Start Docker Desktop from the Windows Start menu or system tray. WSL2 cannot launch Windows GUI applications.

### OAuth authentication

When `rc setup --headless` reaches the OAuth step, it will either open your browser automatically (if `wslu` is installed) or print the URL to paste into your browser. Once you complete the login, send the authorization code back:

```bash
rc send rc-setup "YOUR_AUTH_CODE_HERE"
```

## Troubleshooting

### `docker: command not found`

Docker Desktop WSL integration is not enabled. Go to Docker Desktop → Settings → Resources → WSL Integration and enable your Ubuntu distribution.

### Performance is slow with `/mnt/c/` paths

Move your workspace to the WSL2 filesystem:

```bash
cp -r /mnt/c/Users/you/projects/myapp ~/projects/myapp
rc start ~/projects/myapp
```

### tmux not found

```bash
sudo apt install tmux
```
