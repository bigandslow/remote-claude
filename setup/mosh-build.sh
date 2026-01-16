#!/bin/bash
# Build Mosh from source
# Mosh provides resilient SSH connections that survive network changes

set -e

MOSH_VERSION="1.4.0"
PROTOBUF_VERSION="25.1"

BUILD_DIR="${BUILD_DIR:-/tmp/mosh-build}"
PREFIX="${PREFIX:-/usr/local}"

echo "=== Mosh Build from Source ==="
echo "Version: ${MOSH_VERSION}"
echo "Build directory: ${BUILD_DIR}"
echo "Install prefix: ${PREFIX}"
echo ""

mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

# Detect OS
if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macos"
    NPROC=$(sysctl -n hw.ncpu)
else
    OS="linux"
    NPROC=$(nproc)
fi

echo "Detected OS: $OS"
echo "Build parallelism: $NPROC"
echo ""

# Check for required tools
check_tool() {
    if ! command -v "$1" &> /dev/null; then
        echo "Error: $1 is required but not installed"
        exit 1
    fi
}

check_tool curl
check_tool tar
check_tool make
check_tool g++ || check_tool clang++

# Check for pkg-config
if ! command -v pkg-config &> /dev/null; then
    echo "Warning: pkg-config not found, will try to build without it"
fi

# ============================================================
# Build protobuf (required by Mosh)
# ============================================================
build_protobuf() {
    echo "=== Building protobuf ${PROTOBUF_VERSION} ==="

    PROTOBUF_TAR="protobuf-${PROTOBUF_VERSION}.tar.gz"
    PROTOBUF_URL="https://github.com/protocolbuffers/protobuf/releases/download/v${PROTOBUF_VERSION}/${PROTOBUF_TAR}"

    if [ ! -f "$PROTOBUF_TAR" ]; then
        echo "Downloading protobuf..."
        curl -L -o "$PROTOBUF_TAR" "$PROTOBUF_URL"
    fi

    echo "Extracting..."
    tar xzf "$PROTOBUF_TAR"
    cd "protobuf-${PROTOBUF_VERSION}"

    echo "Configuring protobuf..."
    mkdir -p build && cd build

    # Use cmake for protobuf 25.x
    cmake .. \
        -DCMAKE_INSTALL_PREFIX="$PREFIX" \
        -DCMAKE_BUILD_TYPE=Release \
        -Dprotobuf_BUILD_TESTS=OFF \
        -Dprotobuf_BUILD_SHARED_LIBS=ON

    echo "Building protobuf (this may take a while)..."
    make -j"$NPROC"

    echo ""
    echo "Protobuf built. Run 'sudo make install' to install."
    echo "Or run this script with INSTALL=1 to install automatically."

    if [ "${INSTALL:-0}" = "1" ]; then
        sudo make install
        if [ "$OS" = "linux" ]; then
            sudo ldconfig
        fi
    fi

    cd "$BUILD_DIR"
}

# ============================================================
# Build Mosh
# ============================================================
build_mosh() {
    echo "=== Building Mosh ${MOSH_VERSION} ==="

    MOSH_TAR="mosh-${MOSH_VERSION}.tar.gz"
    MOSH_URL="https://github.com/mobile-shell/mosh/releases/download/mosh-${MOSH_VERSION}/${MOSH_TAR}"

    if [ ! -f "$MOSH_TAR" ]; then
        echo "Downloading mosh..."
        curl -L -o "$MOSH_TAR" "$MOSH_URL"
    fi

    echo "Extracting..."
    tar xzf "$MOSH_TAR"
    cd "mosh-${MOSH_VERSION}"

    echo "Configuring mosh..."

    # Set up paths for dependencies
    export PKG_CONFIG_PATH="${PREFIX}/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
    export LDFLAGS="-L${PREFIX}/lib ${LDFLAGS:-}"
    export CPPFLAGS="-I${PREFIX}/include ${CPPFLAGS:-}"

    if [ "$OS" = "macos" ]; then
        # macOS may need explicit OpenSSL paths if using non-system OpenSSL
        if [ -d "/usr/local/opt/openssl" ]; then
            export LDFLAGS="-L/usr/local/opt/openssl/lib $LDFLAGS"
            export CPPFLAGS="-I/usr/local/opt/openssl/include $CPPFLAGS"
        fi
    fi

    ./configure --prefix="$PREFIX"

    echo "Building mosh..."
    make -j"$NPROC"

    echo ""
    echo "Mosh built. Run 'sudo make install' to install."
    echo "Or run this script with INSTALL=1 to install automatically."

    if [ "${INSTALL:-0}" = "1" ]; then
        sudo make install
    fi

    cd "$BUILD_DIR"
}

# ============================================================
# Main
# ============================================================

# Check if protobuf is already installed
if pkg-config --exists protobuf 2>/dev/null; then
    PROTOBUF_INSTALLED_VERSION=$(pkg-config --modversion protobuf)
    echo "Protobuf already installed: ${PROTOBUF_INSTALLED_VERSION}"
    read -p "Rebuild protobuf? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        build_protobuf
    fi
else
    echo "Protobuf not found, building..."
    build_protobuf
fi

# Check if mosh is already installed
if command -v mosh &> /dev/null; then
    MOSH_INSTALLED_VERSION=$(mosh --version 2>&1 | head -1)
    echo "Mosh already installed: ${MOSH_INSTALLED_VERSION}"
    read -p "Rebuild mosh? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        build_mosh
    fi
else
    echo "Mosh not found, building..."
    build_mosh
fi

echo ""
echo "=== Build Complete ==="
echo ""
echo "If you haven't installed yet, run:"
echo "  cd $BUILD_DIR/protobuf-${PROTOBUF_VERSION}/build && sudo make install"
echo "  cd $BUILD_DIR/mosh-${MOSH_VERSION} && sudo make install"
echo ""
echo "Or re-run with: INSTALL=1 $0"
echo ""
echo "After installation, verify with:"
echo "  mosh --version"
echo ""
echo "Usage:"
echo "  mosh user@host                    # Basic connection"
echo "  mosh user@host -- tmux attach     # Attach to tmux"
echo "  mosh user@host -- tmux -L remote-claude attach  # Attach to rc session"
