#!/usr/bin/env bash
# Commit and push data/ if it changed. Used between workflow steps so a failure or
# cancellation later in the run never loses crawled data or submitted AI batch ids.
# Usage: scripts/commit-data.sh "<message>"   (prints changed=true|false to $GITHUB_OUTPUT if set)
set -uo pipefail
git config user.name "berlin-courses bot"
git config user.email "actions@users.noreply.github.com"
git add data
if git diff --cached --quiet; then
  echo "commit-data: nothing to commit"
  [ -n "${GITHUB_OUTPUT:-}" ] && echo "changed=false" >> "$GITHUB_OUTPUT"
  exit 0
fi
git commit -q -m "$1"
# main may have moved during a long run: replay our data commit on top (our files win on conflicts)
for i in 1 2 3; do
  if git pull -q --rebase -X theirs origin main && git push -q; then
    echo "commit-data: pushed '$1'"
    [ -n "${GITHUB_OUTPUT:-}" ] && echo "changed=true" >> "$GITHUB_OUTPUT"
    exit 0
  fi
  sleep 10
done
echo "::error::commit-data: could not push '$1'"
exit 1
