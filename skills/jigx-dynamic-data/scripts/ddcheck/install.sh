#!/usr/bin/env bash
# Put a `ddcheck` command on PATH that runs this copy, so it works from any repo, branch or
# worktree. Re-run after updating the skill. Nothing else is installed; no login is stored.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="${DDCHECK_BIN:-$HOME/.local/bin}"
mkdir -p "$BIN"
cat > "$BIN/ddcheck" <<EOF
#!/usr/bin/env bash
exec python3 "$SRC" "\$@"
EOF
chmod +x "$BIN/ddcheck"
echo "installed: $BIN/ddcheck -> $SRC"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "add $BIN to your PATH";; esac
