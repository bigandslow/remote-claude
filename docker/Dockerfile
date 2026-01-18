# Remote Claude - Sandboxed Claude Code Container
# Runs Claude Code in YOLO mode with network isolation

FROM ubuntu:22.04

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
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
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 20.x
RUN mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI globally
RUN npm install -g @anthropic-ai/claude-code

# Note: gcloud CLI not installed to keep image small (~500MB savings)
# GCP authentication works via GOOGLE_APPLICATION_CREDENTIALS env var
# which points to the mounted service account key

# Create non-root user for running Claude
RUN useradd -m -s /bin/bash claude \
    && echo "claude ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

# Create workspace directory
RUN mkdir -p /workspace && chown claude:claude /workspace

# Create directories for credential mounts
RUN mkdir -p /home/claude/.anthropic \
    /home/claude/.ssh \
    /home/claude/.gitconfig \
    /home/claude/.claude \
    /home/claude/.config/gcloud \
    && chown -R claude:claude /home/claude

# Copy entrypoint script (must be done as root)
COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh

# Switch to non-root user
USER claude
WORKDIR /workspace

# Set up git to use mounted config
ENV GIT_CONFIG_GLOBAL=/home/claude/.gitconfig

# Default command runs Claude in YOLO mode
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
