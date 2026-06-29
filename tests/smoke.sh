#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== Python syntax ==="
python3 -m py_compile "$ROOT/bin/codex-profile-manager.py"
python3 -m py_compile "$ROOT/bin/send_buttons.py"
python3 -m py_compile "$ROOT/bin/gptprof_autoswitch.py"

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

# Public hygiene: block private/user-specific literals while allowing generic env var names.
if grep -RInE '(Human20|human20|ChipCR|chipmanager|617744661|/home/chip|/opt/telegram-chip|138\.201\.30\.209|157\.180\.97\.244|72\.56\.32\.125|185\.212\.129\.177|gptinvest23|markov495|mynightfly|omnifocusme|mintsage|skills/chip/hcp|CHIP_DM)' "$ROOT" \
  --exclude-dir=.git --exclude=smoke.sh --exclude=*.pyc; then
  echo "Private/operator-specific literal found" >&2
  exit 1
fi

echo "smoke: ok"
