#!/usr/bin/env bash
# Build the Claude Desktop bundle (stage 6 S1): dist/agentodoo-<version>.mcpb.
# Stages only what the uv server needs (manifest, pyproject.toml, README.md, src/, server/main.py,
# icon), validates the manifest and packs it with a pinned MCPB CLI run through npx.
# Usage: scripts/build-mcpb.sh
set -euo pipefail

MCPB_CLI="@anthropic-ai/mcpb@2.1.2"

die() { echo "build-mcpb: $*" >&2; exit 2; }

command -v npx >/dev/null 2>&1 || die "npx is required (install Node.js); nothing was built"
command -v python3 >/dev/null 2>&1 || die "python3 is required; nothing was built"
root=$(cd "$(dirname "$0")/.." && pwd)
version=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$root/src/agentodoo/__init__.py")
[ -n "$version" ] || die "cannot read __version__ from src/agentodoo/__init__.py"

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
python3 - "$root/packaging/mcpb/manifest.json" "$stage/manifest.json" "$version" <<'PY'
import json, sys
source, target, version = sys.argv[1:]
manifest = json.load(open(source, encoding="utf-8"))
manifest["version"] = version
with open(target, "w", encoding="utf-8") as handle:
    json.dump(manifest, handle, indent=2, ensure_ascii=False)
    handle.write("\n")
PY
cp "$root/pyproject.toml" "$root/README.md" "$root/packaging/mcpb/icon.png" "$root/packaging/mcpb/.mcpbignore" "$stage/"
mkdir -p "$stage/server"
cp "$root/packaging/mcpb/server/main.py" "$stage/server/"
(cd "$root" && tar -c --exclude '__pycache__' --exclude '*.py[cod]' --exclude '*.egg-info' src) | tar -x -C "$stage"

mkdir -p "$root/dist"
out="$root/dist/agentodoo-$version.mcpb"
npx --yes "$MCPB_CLI" validate "$stage/manifest.json"
npx --yes "$MCPB_CLI" pack "$stage" "$out"
echo "Built $out ($(wc -c < "$out" | tr -d ' ') bytes)"
