# gptprof-hermes

**Public Hermes skill** для управления ChatGPT-профилями через Telegram-кнопки, `/gptt` / `/mmfast` алиасы, и автоматическое переключение при исчерпании лимита.

![gptprof Telegram profile switcher UI](assets/gptprof-telegram-status.jpg)

---

## 🇷🇺 Русский

### Зачем этот скилл

`gptprof-hermes` — Hermes-нативная обёртка вокруг [gptprof-public](https://github.com/evgyur/gptprof-public) (codex-profile-manager.py). Показывает карточку профиля с **inline-кнопками**, где на каждой кнопке — остаток % по 5-часовому и недельному окну.

### Возможности

| Команда | Что делает |
|---------|-----------|
| `/gptprof` | Карточка профиля с кнопками (остаток % 5ч / нед) |
| `/gptt` | Быстрый переход на `gpt-5.5` через Codex, **persistent** (`--global`) |
| `/mmfast` | Переключает обратно на MiniMax-M2.7 (high reasoning), **persistent** (`--global`) |
| Autoswitch | Автоматически переезжает на профиль с остатком >5%, если текущий исчерпан |

### Установка

```bash
# 1. Склонировать репозиторий
git clone https://github.com/evgyur/gptprof-hermes.git ~/gptprof-hermes

# 2. Скопировать бинарники
cp bin/codex-profile-manager.py ~/.local/bin/codex-profile-manager.py
cp bin/send_buttons.py         ~/.local/bin/send_buttons.py
cp bin/refresh_profiles.py     ~/.local/bin/refresh_profiles.py
chmod 700 ~/.local/bin/codex-profile-manager.py
chmod 700 ~/.local/bin/send_buttons.py
chmod 700 ~/.local/bin/refresh_profiles.py

# 3. Добавить quick_commands в config.yaml (см. ниже)

# 4. /restart — чтобы gateway подхватил новые команды
```

### config.yaml (quick_commands)

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

**Важно:** `--global` в обоих алиасах. Без него модель сбрасывается после рестарта gateway.

### Как работает отображение %

```
5ч остаток  = 100 − primary_window.used_percent
нед остаток = 100 − secondary_window.used_percent
```

Данные берутся из `https://chatgpt.com/backend-api/wham/usage` с кешированием на 15 минут.

### Callback (нажатие кнопки профиля)

**Это происходит на уровне Hermes gateway**, а не в `send_buttons.py`.

При нажатии кнопки `gptprof:<slug>:<model>` Hermes gateway (`gateway/platforms/telegram.py`) выполняет:

1. Копирует `access_token` + `refresh_token` из `~/.hermes/skills/chip/hcp/<slug>.json` в `auth.json → codex`
2. **Пишет глобальный config**: `model=<model>`, `provider=openai-codex` в `config.yaml` — эквивалент `/model <model> --provider openai-codex --global`
3. Устанавливает session override на уровне gateway
4. evict cached agent → рекомендует `/new` для новой сессии

Таким образом после нажатия кнопки профиля:
- Gateway restart **не сбросит** модель обратно (config.yaml записан)
- Сессия начинает использовать новый профиль сразу

### Настройка профилей

Скилл работает с **локальным пулом профилей** в `$HERMES_HCP`:

```
~/.hermes/skills/chip/hcp/
├── gptinvest23.json    ← access_token профиля
├── markov495.json
├── mintsage.json
└── omnifocusme.json
```

Каждый JSON содержит OAuth-токен профиля:

```json
{
  "access_token": "<OAUTH_TOKEN>",
  "expires_at": 1750000000,
  "refresh_token": "<REFRESH_TOKEN>",
  "email": "profile@example.com"
}
```

Активный профиль определяется по `auth.json`:

```json
{
  "codex": {
    "profile": "omnifocusme",
    "access_token": "<ACTIVE_TOKEN>"
  }
}
```

### Локальное обновление OAuth-токенов

`send_buttons.py` сам обновляет access token, если он истекает в ближайшие 48 часов. Для независимости от OpenClaw поставьте локальный timer:

```ini
# /etc/systemd/system/gptprof-token-refresh.service
[Unit]
Description=Refresh Hermes gptprof Codex OAuth tokens
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=hermes
WorkingDirectory=/home/hermes/.hermes/skills/chip/gptprof
Environment=GPTPROF_INTEL64_OPENCLAW_SYNC=0
Environment=GPTPROF_ACCESS_REFRESH_SKEW=172800
ExecStart=/opt/hermes-agent/venv/bin/python3 /home/hermes/.hermes/skills/chip/gptprof/refresh_profiles.py
```

```ini
# /etc/systemd/system/gptprof-token-refresh.timer
[Unit]
Description=Run Hermes gptprof Codex OAuth token refresh periodically

[Timer]
OnBootSec=5min
OnUnitActiveSec=6h
RandomizedDelaySec=15min
Persistent=true
Unit=gptprof-token-refresh.service

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now gptprof-token-refresh.timer
sudo systemctl start gptprof-token-refresh.service
```

`GPTPROF_INTEL64_OPENCLAW_SYNC=1` оставлен только как break-glass импорт, если локальная копия уже протухла. Нормальный путь — локальный `refresh_profiles.py` + systemd timer.

### Autoswitch (автопереключение)

`codex-profile-manager.py` умеет автоматически переключать профиль, если активный достиг 95% по любому окну:

```bash
python3 ~/.local/bin/codex-profile-manager.py autoswitch
```

Логика: если `active.5h_used >= 95%` ИЛИ `active.weekly_used >= 95%`, и есть простой кандидат с остатком >5% по обоим окнам — переезжаем на него.

### Безопасность

- **Никаких токенов в репозитории** — всё локально
- OAuth client ID — публичные метаданные приложения OpenAI, не секрет
- Smoke-тест проверяет паттерны секретов при пуше

```bash
bash tests/smoke.sh
```

---

## 🇬🇧 English

### What This Is

`gptprof-hermes` is a public Hermes skill wrapping [gptprof-public](https://github.com/evgyur/gptprof-public). It shows a Telegram profile card with inline buttons displaying **remaining %** per profile for the 5-hour and weekly windows.

### Quick Start

```bash
git clone https://github.com/evgyur/gptprof-hermes.git ~/gptprof-hermes
cp bin/codex-profile-manager.py ~/.local/bin/
cp bin/send_buttons.py         ~/.local/bin/
cp bin/refresh_profiles.py     ~/.local/bin/
chmod 700 ~/.local/bin/codex-profile-manager.py
chmod 700 ~/.local/bin/send_buttons.py
chmod 700 ~/.local/bin/refresh_profiles.py
```

Add to `config.yaml` → `quick_commands`:

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

Then `/restart` the gateway to pick up new commands.

### Callback Behavior

Button presses (`gptprof:<slug>:<model>`) are handled by Hermes gateway (`gateway/platforms/telegram.py`). On callback:

1. Copies OAuth tokens from `~/.hermes/skills/chip/hcp/<slug>.json` → `auth.json → codex`
2. **Writes global config**: `model=<model>`, `provider=openai-codex` to `config.yaml` (equivalent to `/model <model> --provider openai-codex --global`)
3. Sets session override at gateway level
4. Evicts cached agent → recommends `/new`

This means after pressing a profile button, gateway restarts do **not** reset the model back — the change persists in `config.yaml`.

### How Usage % Is Calculated

```
5h remaining   = 100 − primary_window.used_percent
weekly remain  = 100 − secondary_window.used_percent
```

Fetched from `https://chatgpt.com/backend-api/wham/usage` with 15-minute local cache.

### Security

- **Zero secrets in repo** — all tokens are local
- OAuth client ID is public OpenAI app metadata, not a client secret
- Run `bash tests/smoke.sh` to verify no secrets are committed

### Repository Structure

```
gptprof-hermes/
├── bin/
│   ├── codex-profile-manager.py   # upstream profile manager CLI
│   ├── refresh_profiles.py        # local OAuth refresh timer target
│   └── send_buttons.py            # Hermes-native card sender (sends card only)
├── plugin/
│   ├── index.js                   # OpenClaw plugin bridge (stub)
│   ├── openclaw.plugin.json        # Plugin manifest
│   └── package.json
├── references/
│   └── callback-behavior.md        # details on button callback handling
├── tests/
│   └── smoke.sh                    # syntax + secret-pattern test
├── assets/
│   └── gptprof-telegram-status.jpg
├── README.md
└── SKILL.md
```

---

## Команды / Commands

| Команда | Описание |
|---------|----------|
| `/gptprof` | Показать карточку с кнопками и остатком % |
| `/gptt` | Перейти на gpt-5.5 (Codex route), persistent |
| `/mmfast` | Вернуться на MiniMax-M2.7, persistent |
| `codex-profile-manager.py status` | CLI: показать статус всех профилей |
| `codex-profile-manager.py autoswitch` | CLI: автопереключение при исчерпании |
| `codex-profile-manager.py switch <slug>` | CLI: переключить на профиль |
| `codex-profile-manager.py refresh` | CLI: обновить cache usage |