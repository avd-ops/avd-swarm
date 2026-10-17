#!/usr/bin/env bash
# Fails when a GUID that isn't in scripts/public-ids.txt appears in a tracked file.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
allowed=$(grep -Eo '^[0-9a-fA-F-]{36}' scripts/public-ids.txt | tr 'A-F' 'a-f' | sort -u)
found=$(git ls-files -z | xargs -0 grep -EIoHn '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}' 2>/dev/null \
  | grep -v '^scripts/public-ids.txt:' || true)
bad=0
while IFS= read -r line; do
  [ -z "$line" ] && continue
  id=$(echo "${line##*:}" | tr 'A-F' 'a-f')
  if ! grep -qx "$id" <<<"$allowed"; then
    echo "Hardcoded ID: $line"
    bad=1
  fi
done <<<"$found"
if [ "$bad" -ne 0 ]; then
  echo "Move these to repository variables, or add a public Microsoft ID to scripts/public-ids.txt."
  exit 1
fi
echo "No hardcoded IDs."
