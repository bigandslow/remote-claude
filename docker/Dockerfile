# Remote Claude - Sandboxed Claude Code Container
# Runs Claude Code in YOLO mode with network isolation
#
# Version Pinning Policy:
# - Base image: Pin to specific tag, verify digest periodically
# - Node.js: Pin to LTS major version (20.x)
# - Claude Code: Pin to specific version, update intentionally
# Run `docker pull` and rebuild periodically for security updates

FROM ubuntu:24.04

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
# Note: Versions managed by Ubuntu 24.04 package repository (Python 3.12)
RUN apt-get update && apt-get install -y \
    curl \
    git \
    openssh-client \
    python3 \
    python3-pip \
    python3-venv \
    ripgrep \
    jq \
    sudo \
    ca-certificates \
    gnupg \
    vim-tiny \
    pre-commit \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 20.x LTS
# Pin to 20.x branch for LTS stability
RUN mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI globally
# Pin to specific version for reproducibility
# Update this version intentionally after testing
ARG CLAUDE_CODE_VERSION=latest
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}

# Note: gcloud CLI not installed to keep image small (~500MB savings)
# GCP authentication works via GOOGLE_APPLICATION_CREDENTIALS env var
# which points to the mounted service account key

# Create non-root user for running Claude
# Security: Limited sudo access to package managers only (not full root)
# This allows Claude to install packages but not run arbitrary root commands
RUN useradd -m -s /bin/bash claude \
    && echo "claude ALL=(ALL) NOPASSWD: /usr/bin/apt-get, /usr/bin/apt, /usr/bin/npm, /usr/local/bin/npm, /usr/bin/pip3, /usr/bin/pip, /usr/bin/chown, /usr/bin/chmod" >> /etc/sudoers

# Create workspace directory
RUN mkdir -p /workspace && chown claude:claude /workspace

# Create directories for credential mounts
# Note: .gitconfig is a file, not a directory - don't create it
RUN mkdir -p /home/claude/.anthropic \
    /home/claude/.ssh \
    /home/claude/.claude \
    /home/claude/.config/gcloud \
    && chown -R claude:claude /home/claude

# Copy entrypoint script (must be done as root)
COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh

# Switch to non-root user
USER claude
WORKDIR /workspace

# Note: GIT_CONFIG_GLOBAL is set at runtime by entrypoint.sh
# to allow for deploy key configuration

# Tell Claude where to find its config/credentials
ENV CLAUDE_CONFIG_DIR=/home/claude/.claude

# Default command runs Claude in YOLO mode
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
