---
name: gptprof-hermes
description: "Public Hermes skill: ChatGPT profile card with Telegram inline buttons showing remaining %, /gptt /mmfast aliases (both persistent --global), autoswitch on limit exhaustion. Callback writes global config.yaml — not just session override."
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
| `/gptt` | Switch to `gpt-5.5` via `openai-codex` provider, **persistent** (`--global`) |
| `/mmfast` | Switch back to `MiniMax-M2.7` with high reasoning, **persistent** (`--global`) |
| `/gptprof status` | Full CLI status via `codex-profile-manager.py status` |
| `/gptprof refresh` | Force-refresh usage cache |
| `/gptprof autoswitch` | Run autoswitch logic (switches when active 5h or weekly remaining is ≤5%, or usage/auth error appears) |

## How the Card Works

1. `send_buttons.py` reads profile tokens from `$HERMES_HCP/*.json`
2. Locally refreshes expired/near-expired Codex access tokens through OAuth refresh_token rotation
3. Fetches usage from `https://chatgpt.com/backend-api/wham/usage` for each profile in parallel
4. Computes **remaining %** = `100 − used_percent` for both windows
5. Sends a Telegram `InlineKeyboardMarkup` card to `$GPTPROF_CHAT_ID`
6. Each button carries `callback_data: "gptprof:<slug>:<model>"` (e.g. `gptprof:profile3:gpt-5.5`)
7. After pressing a button → recommended `/new` to reset context

## Callback Behavior (critical)

Button presses are handled by **Hermes gateway** (`gateway/platforms/telegram.py`), not by `send_buttons.py`.

On `gptprof:<slug>:<model>` callback, the gateway:

1. Copies `access_token` + `refresh_token` from `~/.hermes/gptprof/profiles/<slug>.json` → `auth.json → codex`
2. **Writes global config.yaml**:
   ```python
   cfg["model"] = model          # e.g. "gpt-5.5"
   cfg["provider"] = "openai-codex"
   ```
   This is equivalent to `/model <model> --provider openai-codex --global`.
3. Sets session override at gateway level
4. Evicts cached agent

**Why this matters:** Without step 2, gateway restarts would reset the model back to the pre-switch default. With step 2, the model is persisted in `config.yaml` and survives restarts.

See `references/callback-behavior.md` for full details.

## Environment Variables

| Variable | Default | Notes |
|----------|---------|-------|
| `TELEGRAM_BOT_TOKEN` | env | Bot token for sending cards |
| `GPTPROF_CHAT_ID` | env | Telegram chat ID for card delivery |
| `HERMES_AUTH` | `/home/hermes/.hermes/auth.json` | Active profile detection |
| `HERMES_CONFIG` | `/home/hermes/.hermes/config.yaml` | Model name display + global persistence |
| `HERMES_HCP` | `~/.hermes/gptprof/profiles` | Profile token directory |
| `GPTPROF_ACCESS_REFRESH_SKEW` | `172800` | Refresh access tokens this many seconds before expiry |
| `GPTPROF_FORCE_REFRESH` | `0` | Set `1` for one-off validation/rotation |
| `GPTPROF_INTEL64_OPENCLAW_SYNC` | `0` | Break-glass import from OpenClaw; not the primary path |
| `GPTPROF_AUTOSWITCH_THRESHOLD` | `5` | Switch when either 5h or weekly remaining is at/below this % |
| `GPTPROF_AUTOSWITCH_STATE` | `/tmp/gptprof_autoswitch_state.json` | Last switch / no-candidate state |

## Profile Token Directory

Tokens live in `$HERMES_HCP/*.json`, one file per profile slug:

```
~/.hermes/gptprof/profiles/
├── profile1.json
├── profile2.json
└── profile3.json
```

Each file must contain an `access_token` key. The active profile is read from `auth.json`'s `codex.profile` field.

## Local Token Refresh

Hermes can maintain its own OAuth tokens without treating OpenClaw as the canonical source:

```bash
/opt/hermes-agent/venv/bin/python3 ~/.local/bin/refresh_profiles.py
/opt/hermes-agent/venv/bin/python3 ~/.local/bin/refresh_profiles.py --force  # validate/rotate now
```

Recommended systemd timer:

```ini
# /etc/systemd/system/gptprof-token-refresh.service
[Service]
Type=oneshot
User=hermes
Environment=GPTPROF_INTEL64_OPENCLAW_SYNC=0
Environment=GPTPROF_ACCESS_REFRESH_SKEW=172800
ExecStart=/opt/hermes-agent/venv/bin/python3 /home/hermes/.local/bin/refresh_profiles.py
```

```ini
# /etc/systemd/system/gptprof-token-refresh.timer
[Timer]
OnBootSec=5min
OnUnitActiveSec=6h
RandomizedDelaySec=15min
Persistent=true
Unit=gptprof-token-refresh.service

[Install]
WantedBy=timers.target
```

`refresh_token_reused` means the refresh token was already stale before this timer owned it; recover via a fresh device-code auth for that profile.

## Autoswitch cron

Run the autoswitch script every 5 minutes if you want automatic profile rotation before a limit is exhausted:

```bash
*/5 * * * * /opt/hermes-agent/venv/bin/python3 ~/.local/bin/gptprof_autoswitch.py
```

Behavior:

- checks active profile usage for both `primary_window` (5h) and `secondary_window` (weekly);
- switches when either remaining window is `<= GPTPROF_AUTOSWITCH_THRESHOLD` (default `5`);
- also switches if the active profile returns a usage/auth error;
- chooses the healthiest available profile by highest `min(5h_left, week_left)`;
- switches **auth only** and does not change the current model/provider route;
- stays silent on no-op, printing stdout only on a switch or when no healthy alternative exists.

## config.yaml quick_commands Setup

```yaml
quick_commands:
  gptt:
    type: alias
    target: /model gpt-5.5 --provider openai-codex --global
  mmfast:
    type: alias
    target: /model MiniMax-M2.7 --provider minimax --global
  gptprof:
    type: exec
    command: /opt/hermes-agent/venv/bin/python3 ~/.local/bin/send_buttons.py
```

**Both aliases use `--global`** — this is what makes them survive gateway restarts. Without `--global`, the switch is session-only and resets on restart.

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
cp bin/refresh_profiles.py     ~/.local/bin/refresh_profiles.py
chmod 700 ~/.local/bin/codex-profile-manager.py
chmod 700 ~/.local/bin/send_buttons.py
chmod 700 ~/.local/bin/refresh_profiles.py

# Add quick_commands to config.yaml (see above)

# Restart gateway
/restart

# Verify no secrets
bash ~/gptprof-hermes/tests/smoke.sh
```

## Security Notes

- Zero secrets in this repo — all tokens are local to the user's machine
- OAuth client ID (`app_EMoamEEZ73f0CkXaXp7hrann`) is public OpenAI application metadata
- Run `bash tests/smoke.sh` to confirm no tokens were accidentally committed

## Upstream

Built on top of [evgyur/gptprof-public](https://github.com/evgyur/gptprof-public) — the sanitized public version of the profile manager CLI (`codex-profile-manager.py`).

## Output Contract

When this skill is invoked, return one of:

- a rendered Telegram profile card from `bin/send_buttons.py`;
- a concise status/autoswitch result from `bin/codex-profile-manager.py` or `bin/gptprof_autoswitch.py`;
- a setup/configuration checklist that keeps all OAuth tokens and Telegram tokens outside git.

Never print `access_token`, `refresh_token`, Telegram bot tokens, or raw `auth.json` contents in chat output.

## Quick Test Checklist

Before publishing changes:

- [ ] `python3 -m py_compile bin/*.py` passes.
- [ ] `node --check plugin/index.js` passes when Node is available.
- [ ] `bash tests/smoke.sh` passes.
- [ ] Public hygiene scan finds no private operator names, chat IDs, host paths, or real-looking tokens.
- [ ] Any profile examples use placeholders such as `profile1`, not real account slugs or emails.

## Done Criteria

- [ ] Runtime scripts are portable and configured by env vars (`HERMES_AUTH`, `HERMES_HCP`, `GPTPROF_CHAT_ID`).
- [ ] Public docs describe a generic install, not a private deployment.
- [ ] Smoke tests include both syntax checks and public-hygiene checks.
- [ ] Repository has no committed OAuth tokens, bot tokens, private keys, personal chat IDs, or private host paths.
