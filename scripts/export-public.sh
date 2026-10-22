#!/usr/bin/env bash
# Export the public tree: HEAD minus odd/, .atl/ and ignored paths, then check
# every file against a private denylist (stage 5 S2, S3). No git history is copied.
# Usage: scripts/export-public.sh DEST [DENYLIST]
set -euo pipefail

die() { echo "export-public: $*" >&2; exit 2; }

[ $# -ge 1 ] && [ $# -le 2 ] || die "usage: scripts/export-public.sh DEST [DENYLIST]"
dest=$1
root=$(git rev-parse --show-toplevel 2>/dev/null) || die "not inside a git worktree"
denylist=${2:-$root/odd/release/denylist.txt}

[ -f "$denylist" ] || die "denylist not found: $denylist"
if [ -n "$(git -C "$root" status --porcelain --untracked-files=no)" ]; then
  die "uncommitted changes in $root; commit them first (the export uses HEAD)"
fi
if [ -e "$dest" ] && [ -n "$(ls -A "$dest" 2>/dev/null)" ]; then
  die "destination is not empty: $dest"
fi

patterns=$(mktemp)
trap 'rm -f "$patterns"' EXIT
grep -vE '^[[:space:]]*(#|$)' "$denylist" > "$patterns" || true
[ -s "$patterns" ] || die "denylist has no patterns"

# Tracked files that are also ignored are left out, as are odd/ and .atl/.
excludes=(":(exclude)odd" ":(exclude).atl")
while IFS= read -r path; do
  excludes+=(":(exclude,literal)$path")
done < <(git -C "$root" ls-files -ci --exclude-standard)

mkdir -p "$dest"
git -C "$root" archive --format=tar HEAD -- . "${excludes[@]}" | tar -x -C "$dest"

hits=$(cd "$dest" && grep -rnIiE -f "$patterns" . | cut -d: -f1,2 | sed 's|^\./||') || true
if [ -n "$hits" ]; then
  echo "Leak check failed: denylisted terms found at:"
  echo "$hits"
  rm -rf "$dest"
  echo "Removed $dest. Replace the internal data, commit, and export again." >&2
  exit 1
fi

count=$(find "$dest" -type f | wc -l | tr -d ' ')
echo "Exported $count files to $dest; leak check passed."
echo "Next steps:"
echo "  cd $dest && git init -b main && git add -A && git commit -m \"Initial public release\""
