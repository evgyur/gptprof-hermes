---
name: gptprof-hermes
description: "Public Hermes skill: ChatGPT profile card with Telegram inline buttons showing remaining %, /gptt /mmfast aliases, autoswitch on limit exhaustion. Wraps gptprof-public codex-profile-manager.py."
user-invocable: true
disable-model-invocation: true
command-dispatch: tool
command-tool: gptprof
command-arg-mode: raw
---

# gptprof-hermes

Hermes-native skill for ChatGPT profile management: Telegram card with inline buttons showing **remaining %** per profile, `/gptt` / `/mmfast` quick aliases, and autoswitch when the active profile hits its limit.

## Commands

| Slash | Action |
|-------|--------|
| `/gptprof` | Show profile selection card with inline buttons (remaining % 5h / weekly per button) |
| `/gptt` | Switch to `gpt-5.5` via `openai-codex` provider (alias → `/model gpt-5.5 --provider openai-codex`) |
| `/mmfast` | Switch back to `MiniMax-M2.7` with high reasoning (alias → `/model MiniMax-M2.7 --provider minimax --global`) |
| `/gptprof status` | Full CLI status via `codex-profile-manager.py status` |
| `/gptprof refresh` | Force-refresh usage cache |
| `/gptprof autoswitch` | Run autoswitch logic (switches only if active is ≥95% and a healthy spare exists) |

## How the Card Works

1. `send_buttons.py` reads profile tokens from `$HERMES_HCP/*.json`
2. Fetches usage from `https://chatgpt.com/backend-api/wham/usage` for each profile in parallel
3. Computes **remaining %** = `100 − used_percent` for both windows
4. Sends a Telegram `InlineKeyboardMarkup` card to `$CHIP_DM`
5. Each button carries `callback_data: "gptprof:<slug>"`
6. After pressing a button → `/new` to reset context

## Environment Variables

| Variable | Default | Notes |
|----------|---------|-------|
| `TELEGRAM_BOT_TOKEN` | env | Bot token for sending cards |
| `CHIP_DM` | env | Telegram chat ID for card delivery |
| `HERMES_AUTH` | `/home/hermes/.hermes/auth.json` | Active profile detection |
| `HERMES_CONFIG` | `/home/hermes/.hermes/config.yaml` | Model name display |
| `HERMES_HCP` | `/home/hermes/.hermes/skills/chip/hcp` | Profile token directory |

## Profile Token Directory

Tokens live in `$HERMES_HCP/*.json`, one file per profile slug:

```
~/.hermes/skills/chip/hcp/
├── gptinvest23.json
├── markov495.json
├── mintsage.json
└── omnifocusme.json
```

Each file must contain an `access_token` key. The active profile is read from `auth.json`'s `codex.profile` field.

## config.yaml quick_commands Setup

```yaml
quick_commands:
  gptt:
    type: alias
    target: /model gpt-5.5 --provider openai-codex
  mmfast:
    type: alias
    target: /model MiniMax-M2.7 --provider minimax --global
  gptprof:
    type: exec
    command: /opt/hermes-agent/venv/bin/python3 ~/.local/bin/send_buttons.py
```

## Autoswitch Logic

From `codex-profile-manager.py autoswitch`:

- Triggers only when **active profile** hits **≥95%** on either window
- Requires a **healthy spare** (<95% on both windows) to exist
- Schedules a gateway restart after switching so all sessions reload auth
- Safe: if target is also over threshold, no switch happens

## Installation

```bash
# Clone
git clone https://github.com/evgyur/gptprof-hermes.git ~/gptprof-hermes

# Binaries
cp bin/codex-profile-manager.py ~/.local/bin/codex-profile-manager.py
cp bin/send_buttons.py         ~/.local/bin/send_buttons.py
chmod 700 ~/.local/bin/codex-profile-manager.py
chmod 700 ~/.local/bin/send_buttons.py

# Verify no secrets
bash ~/gptprof-hermes/tests/smoke.sh
```

## Security Notes

- Zero secrets in this repo — all tokens are local to the user's machine
- OAuth client ID (`app_EMoamEEZ73f0CkXaXp7hrann`) is public OpenAI application metadata
- Run `bash tests/smoke.sh` to confirm no tokens were accidentally committed

## Upstream

Built on top of [evgyur/gptprof-public](https://github.com/evgyur/gptprof-public) — the sanitized public version of the profile manager CLI (`codex-profile-manager.py`).
