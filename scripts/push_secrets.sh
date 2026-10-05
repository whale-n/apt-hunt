#!/usr/bin/env bash
# Copy the secrets in .env into the GitHub repo's Actions secrets (values never printed).
set -euo pipefail
cd "$(dirname "$0")/.."
REPO=$(python3 -c "import yaml;print(yaml.safe_load(open('config.yaml'))['github']['repo'])" 2>/dev/null || uv run python -c "import yaml;print(yaml.safe_load(open('config.yaml'))['github']['repo'])")
for name in ANTHROPIC_API_KEY NOTION_TOKEN GMAIL_CLIENT_ID GMAIL_CLIENT_SECRET GMAIL_REFRESH_TOKEN NOTIFY_TO RENTER_NAME RENTER_BLURB RENTER_PHONE; do
  value=$(grep -E "^${name}=" .env | head -1 | cut -d= -f2-)
  if [ -z "$value" ]; then echo "skip $name (empty in .env)"; continue; fi
  printf '%s' "$value" | gh secret set "$name" -R "$REPO"
  echo "set $name"
done
