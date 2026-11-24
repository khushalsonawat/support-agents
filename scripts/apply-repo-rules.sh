#!/usr/bin/env bash
# Create or update this repository's branch rulesets from .github/rulesets/*.json.
# Run from the repository root by an admin of the repository:
#   scripts/apply-repo-rules.sh [owner/repo]
set -euo pipefail

repo="${1:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
for file in .github/rulesets/*.json; do
  name=$(jq -r .name "$file")
  id=$(gh api "repos/$repo/rulesets" --jq ".[] | select(.name == \"$name\") | .id")
  if [ -n "$id" ]; then
    gh api -X PUT "repos/$repo/rulesets/$id" --input "$file" >/dev/null
    echo "updated: $name"
  elif gh api -X POST "repos/$repo/rulesets" --input "$file" >/dev/null; then
    echo "created: $name"
  else
    echo "not applied: $name (see the error above)" >&2
  fi
done
