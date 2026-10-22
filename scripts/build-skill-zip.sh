#!/usr/bin/env bash
# Build the skill upload for Claude (stage 6 S5): dist/agentodoo-skill.zip holding agentodoo/SKILL.md.
# Usage: scripts/build-skill-zip.sh
set -euo pipefail

die() { echo "build-skill-zip: $*" >&2; exit 2; }

command -v zip >/dev/null 2>&1 || die "zip is required; nothing was built"
root=$(cd "$(dirname "$0")/.." && pwd)
[ -f "$root/skills/agentodoo/SKILL.md" ] || die "skills/agentodoo/SKILL.md not found"

mkdir -p "$root/dist"
out="$root/dist/agentodoo-skill.zip"
rm -f "$out"
(cd "$root/skills" && zip -q -r -X "$out" agentodoo -x '*/.*' '*/__pycache__/*')
echo "Built $out ($(wc -c < "$out" | tr -d ' ') bytes)"
