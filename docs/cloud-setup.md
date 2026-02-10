# Cloud Setup

Run sessions on cloud VMs (DigitalOcean or Hetzner) via Tailscale mesh networking.

## Prerequisites

- [Tailscale](https://tailscale.com) installed and running on your local machine
- Tailscale CLI (`tailscale`) available on PATH (see [macOS CLI setup](#macos-tailscale-cli) below)
- [1Password CLI](https://developer.1password.com/docs/cli/) (`op`) configured
- A DigitalOcean or Hetzner Cloud account

## 1. Cloud Provider API Token

### DigitalOcean

1. Go to [API → Tokens](https://cloud.digitalocean.com/account/api/tokens)
2. Click **Generate New Token**
3. Name it (e.g., `remote-claude`)
4. Under **Custom Scopes**, select:
   - **Droplets → Create**
   - **Droplets → Delete**
5. Click **Generate Token** and copy the value
6. Store in 1Password — note the `op://` reference URI

### Hetzner

1. Go to your project → **Security → API Tokens**
2. Click **Generate API Token**
3. Name it (e.g., `remote-claude`) with **Read & Write** permissions
4. Copy the token value
5. Store in 1Password — note the `op://` reference URI

### Verify token access

```bash
op read "op://YourVault/YourItem/token-field"
```

## 2. Tailscale OAuth Client

The OAuth client lets rc generate one-time auth keys so new VMs automatically join your tailnet.

### Create an ACL tag

Before creating the OAuth client, add a `tag:cloud` tag to your Tailscale ACL policy:

1. Go to [Access Controls](https://login.tailscale.com/admin/acls/file)
2. Add to the `tagOwners` section:
   ```json
   "tagOwners": {
       "tag:cloud": ["autogroup:admin"]
   }
   ```
3. Save

### Create the OAuth client

1. Go to [Settings → OAuth clients](https://login.tailscale.com/admin/settings/oauth)
2. Click **Generate OAuth client**
3. Grant these scopes:
   - **Devices → Core** — Write (to register new VMs on the tailnet)
   - **Keys** — Write (to create one-time auth keys)
4. Click **Generate**
5. Copy both the **Client ID** and **Client Secret**
6. Store the **Client Secret** in 1Password — note the `op://` reference URI

The Client ID looks like `k1234567890abcdef`. The secret is shown once.

## 3. Configure

Add the cloud section to `~/.config/remote-claude/config.yaml`:

```yaml
cloud:
  enabled: true

  # "digitalocean" or "hetzner"
  provider: digitalocean

  # API token stored in 1Password
  api_token_ref: "op://YourVault/DigitalOcean/API Key"

  # Tailscale OAuth (for auto-provisioning VM auth keys)
  tailscale_oauth_client_id: "k1234567890abcdef"
  tailscale_oauth_secret_ref: "op://YourVault/Tailscale/oauth-secret"

  # Optional: override provider defaults
  # default_server_type: s-2vcpu-4gb   # DO default
  # default_region: nyc                 # DO default
  placement: auto
  max_sessions_per_node: 6
```

### Provider defaults

| Setting | DigitalOcean | Hetzner |
|---------|-------------|---------|
| **Server type** | `s-2vcpu-4gb` ($24/mo) | `cpx21` ($8.99/mo) |
| **Region** | `nyc3` | `ash` (Ashburn) |

### Available regions

**DigitalOcean:** `nyc`, `sfo`, `lon`, `ams`, `sgp`, `tor`, `blr`, `syd`

**Hetzner:** `ash` (Ashburn), `hil` (Hillsboro), `fsn` (Falkenstein), `nbg` (Nuremberg), `hel` (Helsinki)

### Available server types

**DigitalOcean:**
| Type | vCPUs | RAM | Cost |
|------|-------|-----|------|
| `s-1vcpu-1gb` | 1 | 1 GB | $6/mo |
| `s-1vcpu-2gb` | 1 | 2 GB | $12/mo |
| `s-2vcpu-2gb` | 2 | 2 GB | $18/mo |
| `s-2vcpu-4gb` | 2 | 4 GB | $24/mo |
| `s-4vcpu-8gb` | 4 | 8 GB | $48/mo |
| `s-8vcpu-16gb` | 8 | 16 GB | $96/mo |

**Hetzner:**
| Type | vCPUs | RAM | Cost |
|------|-------|-----|------|
| `cpx11` | 2 | 2 GB | $4.35/mo |
| `cpx21` | 3 | 4 GB | $8.99/mo |
| `cpx31` | 4 | 8 GB | $16.49/mo |
| `cpx41` | 8 | 16 GB | $30.49/mo |
| `cpx51` | 16 | 32 GB | $61.49/mo |

## 4. Provision and use

```bash
# Add a cloud node
rc cloud node add --name rc-1

# Check status
rc cloud node list
rc cloud status

# Start a session on cloud
rc start ~/projects/myapp -C

# Or specify a node
rc start ~/projects/myapp --node rc-1

# Move existing session to cloud
rc teleport myapp --to cloud

# Move back to local
rc teleport myapp --to local

# Destroy a node
rc cloud node remove rc-1
```

## macOS Tailscale CLI

The Mac App Store version of Tailscale includes the CLI binary but it crashes when invoked
via symlink due to a bundle identifier check. The fix is to use a shell wrapper instead:

```bash
# If you have a symlink, remove it
sudo rm /usr/local/bin/tailscale

# Create a wrapper script
sudo tee /usr/local/bin/tailscale > /dev/null << 'EOF'
#!/bin/sh
exec /Applications/Tailscale.app/Contents/MacOS/Tailscale "$@"
EOF
sudo chmod +x /usr/local/bin/tailscale
```

Verify it works:

```bash
tailscale ip -4    # Should print your Tailscale IP
```

## Troubleshooting

### DigitalOcean: `403 Forbidden` / `missing the required permission`

The API token is missing required scopes. Generate a new token at
[API → Tokens](https://cloud.digitalocean.com/account/api/tokens) with
**Custom Scopes → Droplets → Create** and **Droplets → Delete**.

### Tailscale: `Failed to create Tailscale auth key`

The OAuth client is missing scopes. Regenerate at
[Settings → OAuth clients](https://login.tailscale.com/admin/settings/oauth) with
**Devices → Core → Write** and **Keys → Write**.

## Migrating from Hetzner-only config

If you have an existing config with `hetzner_api_token_ref`, it still works.
To migrate explicitly:

1. Rename `hetzner_api_token_ref` to `api_token_ref`
2. Add `provider: hetzner`

Old configs without a `provider` field default to `digitalocean`.
