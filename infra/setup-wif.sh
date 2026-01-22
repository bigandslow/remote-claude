#!/bin/bash
#
# Remote Claude - WIF Infrastructure Setup
#
# Deploys Workload Identity Federation infrastructure using Pulumi
# and generates the credential configuration file.
#
# Prerequisites:
#   - gcloud CLI authenticated
#   - pulumi CLI installed
#   - Python 3.9+
#
# Usage:
#   ./infra/setup-wif.sh <project-id> [allowed-users]
#
# Example:
#   ./infra/setup-wif.sh my-gcp-project user@example.com,other@example.com

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CREDS_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/remote-claude/credentials/gcp"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }
header() { echo -e "\n${BLUE}=== $1 ===${NC}\n"; }

# Check prerequisites
check_prerequisites() {
    header "Checking Prerequisites"

    if ! command -v gcloud &> /dev/null; then
        error "gcloud CLI not found. Install from https://cloud.google.com/sdk/docs/install"
        exit 1
    fi
    info "gcloud CLI: $(gcloud version 2>/dev/null | head -1)"

    if ! command -v pulumi &> /dev/null; then
        error "pulumi CLI not found. Install from https://www.pulumi.com/docs/install/"
        exit 1
    fi
    info "pulumi CLI: $(pulumi version)"

    if ! command -v python3 &> /dev/null; then
        error "Python 3 not found"
        exit 1
    fi
    info "Python: $(python3 --version)"

    # Check gcloud auth
    if ! gcloud auth print-identity-token &> /dev/null; then
        error "Not authenticated with gcloud. Run: gcloud auth login"
        exit 1
    fi
    info "gcloud auth: OK"
}

# Setup Python virtual environment
setup_venv() {
    header "Setting Up Python Environment"

    cd "$SCRIPT_DIR"

    if [ ! -d "venv" ]; then
        info "Creating virtual environment..."
        python3 -m venv venv
    fi

    info "Installing dependencies..."
    source venv/bin/activate
    pip install -q -r requirements.txt
}

# Deploy infrastructure
deploy_infrastructure() {
    local project_id="$1"
    local allowed_users="$2"
    local stack_name="$3"

    header "Deploying WIF Infrastructure"

    cd "$SCRIPT_DIR"
    source venv/bin/activate

    # Initialize or select stack
    if pulumi stack ls 2>/dev/null | grep -q "$stack_name"; then
        info "Selecting existing stack: $stack_name"
        pulumi stack select "$stack_name"
    else
        info "Creating new stack: $stack_name"
        pulumi stack init "$stack_name"
    fi

    # Set configuration
    info "Configuring stack..."
    pulumi config set project_id "$project_id"
    pulumi config set gcp:project "$project_id"

    if [ -n "$allowed_users" ]; then
        pulumi config set allowed_users "$allowed_users"
        info "Allowed users: $allowed_users"
    fi

    # Preview changes
    echo ""
    info "Previewing changes..."
    pulumi preview

    echo ""
    read -p "Deploy these changes? [y/N]: " confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        info "Deployment cancelled"
        exit 0
    fi

    # Deploy
    info "Deploying..."
    pulumi up --yes

    info "Infrastructure deployed successfully!"
}

# Generate credential configuration
generate_credential_config() {
    local project_id="$1"

    header "Generating Credential Configuration"

    cd "$SCRIPT_DIR"
    source venv/bin/activate

    # Get outputs from Pulumi
    local project_number=$(pulumi stack output project_number)
    local pool_id=$(pulumi stack output workload_identity_pool_id)
    local provider_id=$(pulumi stack output workload_identity_provider_id)
    local sa_email=$(pulumi stack output service_account_email)

    # Create credentials directory
    mkdir -p "$CREDS_DIR"

    local cred_file="$CREDS_DIR/wif-credential-config.json"
    local audience="//iam.googleapis.com/projects/${project_number}/locations/global/workloadIdentityPools/${pool_id}/providers/${provider_id}"

    info "Generating credential config..."
    info "  Pool: $pool_id"
    info "  Provider: $provider_id"
    info "  Service Account: $sa_email"

    # Generate credential configuration using gcloud
    gcloud iam workload-identity-pools create-cred-config \
        "projects/${project_number}/locations/global/workloadIdentityPools/${pool_id}/providers/${provider_id}" \
        --service-account="$sa_email" \
        --output-file="$cred_file" \
        --executable-output-file=/dev/stdout \
        --executable-command="gcloud auth print-identity-token --audiences=$audience"

    chmod 600 "$cred_file"

    info "Credential config saved to: $cred_file"

    # Create symlink for docker_manager compatibility
    ln -sf "wif-credential-config.json" "$CREDS_DIR/claude-sa-key.json"

    # Update remote-claude config
    update_remote_claude_config "$cred_file"
}

# Update remote-claude config.yaml
update_remote_claude_config() {
    local cred_file="$1"
    local config_file="${XDG_CONFIG_HOME:-$HOME/.config}/remote-claude/config.yaml"

    if [ -f "$config_file" ]; then
        info "Updating remote-claude config..."
        python3 << EOF
import yaml
from pathlib import Path

config_file = Path("$config_file")
config = yaml.safe_load(config_file.read_text())

if "credentials" not in config:
    config["credentials"] = {}

config["credentials"]["claude_gcp"] = "$cred_file"

config_file.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))
print("  Updated claude_gcp in config.yaml")
EOF
    fi
}

# Test the configuration
test_configuration() {
    header "Testing Configuration"

    local cred_file="$CREDS_DIR/wif-credential-config.json"

    if [ ! -f "$cred_file" ]; then
        error "Credential config not found: $cred_file"
        return 1
    fi

    info "Testing WIF authentication..."

    # Set the credential file and try to get an access token
    export GOOGLE_APPLICATION_CREDENTIALS="$cred_file"

    if gcloud auth application-default print-access-token &> /dev/null; then
        info "WIF authentication: SUCCESS"

        # Show the identity
        local sa_email=$(pulumi stack output service_account_email 2>/dev/null || echo "unknown")
        info "Authenticated as: $sa_email"
    else
        error "WIF authentication: FAILED"
        echo ""
        echo "Troubleshooting:"
        echo "  1. Ensure you're logged in: gcloud auth login"
        echo "  2. Check your email is in the allowed_users list"
        echo "  3. Verify IAM bindings in GCP Console"
        return 1
    fi
}

# Main
main() {
    local project_id="${1:-}"
    local allowed_users="${2:-}"
    local stack_name="${3:-dev}"

    echo "Remote Claude - WIF Infrastructure Setup"
    echo "========================================="

    if [ -z "$project_id" ]; then
        echo ""
        echo "Usage: $0 <project-id> [allowed-users] [stack-name]"
        echo ""
        echo "Arguments:"
        echo "  project-id     GCP project ID"
        echo "  allowed-users  Comma-separated list of user emails (optional)"
        echo "  stack-name     Pulumi stack name (default: dev)"
        echo ""
        echo "Example:"
        echo "  $0 my-project user@example.com"
        echo ""

        # Interactive mode
        read -p "GCP Project ID: " project_id
        if [ -z "$project_id" ]; then
            error "Project ID is required"
            exit 1
        fi

        read -p "Allowed user emails (comma-separated, or Enter for all): " allowed_users
    fi

    check_prerequisites
    setup_venv
    deploy_infrastructure "$project_id" "$allowed_users" "$stack_name"
    generate_credential_config "$project_id"
    test_configuration

    header "Setup Complete"
    echo "WIF infrastructure has been deployed and configured."
    echo ""
    echo "The credential configuration has been saved to:"
    echo "  $CREDS_DIR/wif-credential-config.json"
    echo ""
    echo "Remote Claude containers will now authenticate as the limited"
    echo "service account without having access to your personal credentials."
    echo ""
    echo "To add more users, run:"
    echo "  cd $SCRIPT_DIR && pulumi config set allowed_users \"user1@example.com,user2@example.com\" && pulumi up"
}

main "$@"
