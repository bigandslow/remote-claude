"""
Remote Claude - Workload Identity Federation Infrastructure

This Pulumi program creates:
1. A limited service account for Claude containers
2. A Workload Identity Pool and Provider
3. IAM bindings to allow WIF to impersonate the service account

The credential configuration file is generated separately after deployment.
"""

import pulumi
from pulumi_gcp import iam, serviceaccount, organizations, projects

# Configuration
config = pulumi.Config()
project_id = config.require("project_id")
region = config.get("region") or "global"

# Get project number (needed for WIF)
project = organizations.get_project(project_id=project_id)
project_number = project.number

# ============================================================================
# Service Account (limited permissions for Claude)
# ============================================================================

claude_sa = serviceaccount.Account(
    "claude-agent",
    account_id="claude-agent",
    display_name="Claude Agent (Remote Claude)",
    description="Limited service account for Remote Claude YOLO sessions",
    project=project_id,
)

# Grant limited roles to the service account
# Customize these based on what Claude needs to do
sa_roles = [
    "roles/viewer",                    # Read-only access to most resources
    "roles/run.developer",             # Deploy to Cloud Run (no delete)
    "roles/storage.objectViewer",      # Read GCS objects
    "roles/storage.objectCreator",     # Create new GCS objects (no overwrite/delete)
    "roles/logging.viewer",            # View logs
    "roles/cloudsql.client",           # Connect to Cloud SQL
]

for i, role in enumerate(sa_roles):
    iam.IAMMember(
        f"claude-sa-role-{i}",
        project=project_id,
        role=role,
        member=claude_sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

# ============================================================================
# Workload Identity Pool
# ============================================================================

wif_pool = iam.WorkloadIdentityPool(
    "remote-claude-pool",
    workload_identity_pool_id="remote-claude-pool",
    display_name="Remote Claude Pool",
    description="Workload Identity Pool for Remote Claude containers",
    project=project_id,
    disabled=False,
)

# ============================================================================
# Workload Identity Provider (Google OIDC for local gcloud auth)
# ============================================================================

# This provider accepts Google-issued identity tokens from gcloud auth
wif_provider = iam.WorkloadIdentityPoolProvider(
    "google-oidc-provider",
    workload_identity_pool_id=wif_pool.workload_identity_pool_id,
    workload_identity_pool_provider_id="google-oidc",
    display_name="Google OIDC (gcloud auth)",
    description="Accepts identity tokens from gcloud auth print-identity-token",
    project=project_id,
    # OIDC configuration for Google-issued tokens
    oidc=iam.WorkloadIdentityPoolProviderOidcArgs(
        issuer_uri="https://accounts.google.com",
        allowed_audiences=[
            # Default audience for gcloud identity tokens
            f"//iam.googleapis.com/projects/{project_number}/locations/global/workloadIdentityPools/remote-claude-pool/providers/google-oidc",
        ],
    ),
    # Map Google identity claims
    attribute_mapping={
        "google.subject": "assertion.sub",
        "attribute.email": "assertion.email",
        "attribute.email_verified": "assertion.email_verified",
    },
    # Optional: Restrict to verified emails only
    attribute_condition='attribute.email_verified == "true"',
)

# ============================================================================
# IAM Binding: Allow WIF pool to impersonate the service account
# ============================================================================

# Get the list of allowed users from config (comma-separated emails)
allowed_users = config.get("allowed_users") or ""
allowed_user_list = [u.strip() for u in allowed_users.split(",") if u.strip()]

# Build the principal set for WIF
# This allows any identity from the pool that matches the attribute condition
wif_principal_set = pulumi.Output.all(project_number, wif_pool.workload_identity_pool_id).apply(
    lambda args: f"principalSet://iam.googleapis.com/projects/{args[0]}/locations/global/workloadIdentityPools/{args[1]}/attribute.email_verified/true"
)

# If specific users are configured, create individual bindings
if allowed_user_list:
    for i, user_email in enumerate(allowed_user_list):
        # Create a principal for this specific user's identity in the pool
        user_principal = pulumi.Output.all(project_number, wif_pool.workload_identity_pool_id).apply(
            lambda args, email=user_email: f"principal://iam.googleapis.com/projects/{args[0]}/locations/global/workloadIdentityPools/{args[1]}/subject/{email}"
        )

        serviceaccount.IAMMember(
            f"wif-sa-impersonation-user-{i}",
            service_account_id=claude_sa.name,
            role="roles/iam.workloadIdentityUser",
            member=user_principal,
        )
else:
    # Allow all verified emails from the pool (broader access)
    serviceaccount.IAMMember(
        "wif-sa-impersonation",
        service_account_id=claude_sa.name,
        role="roles/iam.workloadIdentityUser",
        member=wif_principal_set,
    )

# ============================================================================
# Outputs
# ============================================================================

pulumi.export("project_id", project_id)
pulumi.export("project_number", project_number)
pulumi.export("service_account_email", claude_sa.email)
pulumi.export("workload_identity_pool_id", wif_pool.workload_identity_pool_id)
pulumi.export("workload_identity_pool_name", wif_pool.name)
pulumi.export("workload_identity_provider_id", wif_provider.workload_identity_pool_provider_id)
pulumi.export("workload_identity_provider_name", wif_provider.name)

# Output the credential config command for convenience
pulumi.export("credential_config_command", pulumi.Output.all(
    project_number,
    wif_pool.workload_identity_pool_id,
    wif_provider.workload_identity_pool_provider_id,
    claude_sa.email,
).apply(lambda args: f"""
gcloud iam workload-identity-pools create-cred-config \\
  projects/{args[0]}/locations/global/workloadIdentityPools/{args[1]}/providers/{args[2]} \\
  --service-account={args[3]} \\
  --output-file=~/.config/remote-claude/credentials/gcp/wif-credential-config.json \\
  --executable-output-file=/dev/stdout \\
  --executable-command="gcloud auth print-identity-token --audiences=//iam.googleapis.com/projects/{args[0]}/locations/global/workloadIdentityPools/{args[1]}/providers/{args[2]}"
"""))
