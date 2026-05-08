# Callback Behavior — gptprof-hermes

## Overview

When a user presses a profile button in the Telegram card, the callback `gptprof:<slug>:<model>` is handled by **Hermes gateway**, not by `send_buttons.py`.

The gateway code lives in:
```
hermes-agent/gateway/platforms/telegram.py
  → _handle_gptprof_callback(query, profile, model)
```

## Callback Data Format

**Old format (broken):** `gptprof:omnifocusme`
**Current format (correct):** `gptprof:omnifocusme:gpt-5.4-mini`

The `:model` suffix is required — without it, the gateway cannot determine which GPT model to switch to.

## send_buttons.py Callback Data

The card must use:

```python
callback_data=f"gptprof:{slug}:{model}"
```

Where `model` is one of: `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`.

## Gateway Handler Flow

When `callback_data = "gptprof:omnifocusme:gpt-5.4-mini"` is received:

```python
parts = data.split(":")
# ["gptprof", "omnifocusme", "gpt-5.4-mini"]
_, profile, model = parts[:3]
await _handle_gptprof_callback(query, profile, model)
```

Inside `_handle_gptprof_callback`:

1. **Copy OAuth tokens** — reads `~/.hermes/skills/chip/hcp/<profile>.json`, copies `access_token` + `refresh_token` to `auth.json → codex`

2. **Write global config.yaml** (critical step):
   ```python
   cfg["model"] = model          # "gpt-5.4-mini"
   cfg["provider"] = "openai-codex"
   with open(config_path, "w") as f:
       yaml.safe_dump(cfg, f)
   ```
   This persists the model switch across gateway restarts.

3. **Set session override** — updates `_session_model_overrides[session_key]` so the current session immediately uses the new model.

4. **Evict cached agent** — calls `_evict_cached_agent(session_key)` to force fresh agent creation with new model on next turn.

5. **Confirm to user** — shows alert and edits message:
   ```
   ✅ Профиль активирован
   `omnifocusme` (Plus $20)
   Модель: `gpt-5.4-mini`
   ✅ Сохранено глобально в `config.yaml`.
   Нажми `/new` для новой сессии с выбранным GPT.
   ```

## Why Global Config Write Matters

Without step 2, the model switch would only survive until the next gateway restart. After restart, Hermes would read `config.yaml` → `model: minimax` (or whatever the default is) and reset the model.

With step 2, `config.yaml` now contains:
```yaml
model: gpt-5.4-mini
provider: openai-codex
```

So after any restart, Hermes starts with the selected GPT model, not the old default.

## Session vs Global Distinction

| Action | Survives Restart? |
|--------|------------------|
| Session override (step 3) | ❌ — lost on restart |
| `config.yaml` write (step 2) | ✅ — persists |
| OAuth token copy (step 1) | ✅ — tokens are stored in `auth.json` which survives restarts |

Both session override AND config.yaml write are needed:
- Session override → immediate effect in current session
- config.yaml write → effect after restart

## Gateway Restart Caveat

After changing `config.yaml`, the gateway must be restarted to reload the new values. However, the session override handles immediate use. The user should `/new` for a clean session after pressing a button.

## Related Files

- `/opt/hermes-agent/gateway/platforms/telegram.py` — `_handle_gptprof_callback`
- `/opt/hermes-agent/gateway/run.py` — `/model --global` persistence logic
- `bin/send_buttons.py` — upstream card sender (callback_data format only)