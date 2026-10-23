#!/usr/bin/env bash
# Copies the three values the daily calendar refresh needs from your local .env into GitHub Actions secrets.
# Run it yourself from the project folder:   ./scripts/set_github_secrets.sh
# Needs the GitHub CLI (gh) logged in. Values are piped straight from .env to GitHub; they are never printed.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v gh >/dev/null || { echo "Install the GitHub CLI first: brew install gh && gh auth login"; exit 1; }
[ -f .env ] || { echo ".env not found in $(pwd)"; exit 1; }
REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
echo "Setting secrets on $REPO"
for NAME in SUPABASE_URL SUPABASE_SERVICE_ROLE_KEY NUEVAI_CALENDAR_URL; do
  VALUE="$(grep -E "^${NAME}=" .env | head -1 | cut -d= -f2- | sed -e 's/^["'"'"']//' -e 's/["'"'"']$//')"
  if [ -z "$VALUE" ]; then echo "  ! ${NAME} is missing from .env - skipped"; continue; fi
  printf '%s' "$VALUE" | gh secret set "$NAME" --repo "$REPO" >/dev/null && echo "  ok ${NAME}"
done
echo "Done. Check them at: https://github.com/${REPO}/settings/secrets/actions"
echo "Then run the workflow once: gh workflow run 'Refresh calendar' --repo ${REPO}"
