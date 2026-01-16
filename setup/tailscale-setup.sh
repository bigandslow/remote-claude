#!/bin/bash
# Tailscale Setup for Remote Claude
# Downloads and installs Tailscale without package managers

set -e

TAILSCALE_VERSION="1.76.6"

echo "=== Tailscale Setup for Remote Claude ==="
echo ""

detect_os() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "macos"
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        echo "linux"
    else
        echo "unknown"
    fi
}

OS=$(detect_os)

case "$OS" in
    macos)
        echo "Detected macOS"
        echo ""
        echo "Tailscale for macOS is distributed as an app bundle."
        echo "Download options:"
        echo ""
        echo "1. Direct download (recommended):"
        echo "   https://pkgs.tailscale.com/stable/Tailscale-${TAILSCALE_VERSION}-macos.pkg"
        echo ""
        echo "2. App Store:"
        echo "   https://apps.apple.com/app/tailscale/id1475387142"
        echo ""
        echo "After installation:"
        echo "  1. Open Tailscale from Applications"
        echo "  2. Click 'Log in' and authenticate"
        echo "  3. Enable 'Allow incoming connections' in preferences"
        echo ""

        read -p "Download the PKG installer now? [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            PKG_URL="https://pkgs.tailscale.com/stable/Tailscale-${TAILSCALE_VERSION}-macos.pkg"
            PKG_FILE="/tmp/Tailscale-${TAILSCALE_VERSION}-macos.pkg"
            echo "Downloading ${PKG_URL}..."
            curl -L -o "$PKG_FILE" "$PKG_URL"
            echo ""
            echo "Downloaded to: $PKG_FILE"
            echo "Run: sudo installer -pkg $PKG_FILE -target /"
            echo "Or double-click the .pkg file to install via GUI"
        fi
        ;;

    linux)
        echo "Detected Linux"
        echo ""

        ARCH=$(uname -m)
        case "$ARCH" in
            x86_64) TAILSCALE_ARCH="amd64" ;;
            aarch64) TAILSCALE_ARCH="arm64" ;;
            armv7l) TAILSCALE_ARCH="arm" ;;
            *) echo "Unsupported architecture: $ARCH"; exit 1 ;;
        esac

        echo "Architecture: $ARCH ($TAILSCALE_ARCH)"
        echo ""

        TARBALL="tailscale_${TAILSCALE_VERSION}_${TAILSCALE_ARCH}.tgz"
        URL="https://pkgs.tailscale.com/stable/${TARBALL}"

        read -p "Download and install Tailscale ${TAILSCALE_VERSION}? [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            echo "Downloading ${URL}..."
            curl -L -o "/tmp/${TARBALL}" "$URL"

            echo "Extracting..."
            cd /tmp
            tar xzf "${TARBALL}"

            EXTRACT_DIR="tailscale_${TAILSCALE_VERSION}_${TAILSCALE_ARCH}"

            echo "Installing to /usr/local/bin..."
            sudo cp "${EXTRACT_DIR}/tailscale" /usr/local/bin/
            sudo cp "${EXTRACT_DIR}/tailscaled" /usr/local/bin/
            sudo chmod +x /usr/local/bin/tailscale /usr/local/bin/tailscaled

            echo ""
            echo "Creating systemd service..."
            sudo tee /etc/systemd/system/tailscaled.service > /dev/null << 'EOF'
[Unit]
Description=Tailscale node agent
Documentation=https://tailscale.com/kb/
After=network-pre.target

[Service]
ExecStart=/usr/local/bin/tailscaled --state=/var/lib/tailscale/tailscaled.state --socket=/run/tailscale/tailscaled.sock
ExecStopPost=/usr/bin/rm -rf /run/tailscale
Restart=on-failure
RuntimeDirectory=tailscale
StateDirectory=tailscale

[Install]
WantedBy=multi-user.target
EOF

            echo "Enabling and starting tailscaled..."
            sudo systemctl daemon-reload
            sudo systemctl enable tailscaled
            sudo systemctl start tailscaled

            echo ""
            echo "Tailscale installed. Now authenticate:"
            echo "  sudo tailscale up --ssh"
            echo ""
            echo "The --ssh flag enables Tailscale SSH (optional but recommended)"
        fi
        ;;

    *)
        echo "Unsupported OS: $OSTYPE"
        exit 1
        ;;
esac

echo ""
echo "=== Post-Installation Steps ==="
echo ""
echo "1. Authenticate: tailscale up (or via GUI on macOS)"
echo "2. Check status: tailscale status"
echo "3. Get your Tailscale IP: tailscale ip -4"
echo "4. Your machine name: $(hostname).tail***.ts.net"
echo ""
echo "From another device on your tailnet:"
echo "  ssh user@$(hostname).tail***.ts.net"
echo "  mosh user@$(hostname).tail***.ts.net"
