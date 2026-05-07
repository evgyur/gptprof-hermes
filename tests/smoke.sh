#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== Python syntax ==="
python3 -m py_compile "$ROOT/bin/codex-profile-manager.py"
python3 -m py_compile "$ROOT/bin/send_buttons.py"

echo "=== JS syntax ==="
if command -v node >/dev/null 2>&1; then
  node --check "$ROOT/plugin/index.js"
fi

# Only flag real-looking tokens (sk-, gho_, real JWT eyJ...)
# Allow placeholder syntax: <TOKEN>, ey..., null, "", etc.
if grep -RInE '(sk-[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{30,}|"refresh_token"\s*:\s*"[^"<]{20,}"|"access_token"\s*:\s*"eyJ[a-zA-Z0-9_-]{40,}")' "$ROOT" \
  --exclude-dir=.git --exclude=smoke.sh --exclude=*.pyc; then
  echo "Potential secret found" >&2
  exit 1
fi

echo "smoke: ok"
