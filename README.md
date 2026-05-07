# gptprof-hermes

**Public Hermes skill** для управления ChatGPT-профилями через Telegram-кнопки, `/gptt` / `/mmfast` алиасы, и автоматическое переключение при исчерпании лимита.

![gptprof Telegram profile switcher UI](assets/gptprof-telegram-status.jpg)

---

## 🇷🇺 Русский

### Зачем этот скилл

`gptprof-hermes` — это Hermes-нативная обёртка вокруг [gptprof-public](https://github.com/evgyur/gptprof-public) (codex-profile-manager.py). Он показывает карточку профиля с **inline-кнопками**, где на каждой кнопке — остаток % по 5-часовому и недельному окну.

### Возможности

| Команда | Что делает |
|---------|-----------|
| `/gptprof` | Карточка профиля с кнопками (остаток % 5ч / нед) |
| `/gptt` | Быстрый переход на `gpt-5.5` через Codex (MiniMax-аналог: `/mmfast`) |
| `/mmfast` | Переключает обратно на MiniMax-M2.7 (high reasoning) |
| Autoswitch | Автоматически переезжает на профиль с остатком >5%, если текущий исчерпан |

### Установка

```bash
# 1. Склонить репозиторий
git clone https://github.com/evgyur/gptprof-hermes.git ~/gptprof-hermes

# 2. Скопировать бинарники
cp bin/codex-profile-manager.py ~/.local/bin/codex-profile-manager.py
cp bin/send_buttons.py         ~/.local/bin/send_buttons.py
chmod 700 ~/.local/bin/codex-profile-manager.py
chmod 700 ~/.local/bin/send_buttons.py

# 3. Добавить quick_commands в config.yaml
```

### config.yaml (quick_commands)

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

### Настройка профилей

Скилл работает с **локальным пулом профилей** в `$HERMES_HCP`:

```
~/.hermes/skills/chip/hcp/
├── gptinvest23.json    ← access_token профиля
├── markov495.json
├── mintsage.json
└── omnifocusme.json
```

Каждый JSON содержит OAuth-токен профиля. Структура:

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

### Как работает отображение %

```
5ч остаток  = 100 − primary_window.used_percent
нед остаток = 100 − secondary_window.used_percent
```

Данные берутся из `https://chatgpt.com/backend-api/wham/usage` с кешированием на 15 минут.

### Кнопки в Telegram

Каждая кнопка — это `callback_data: "gptprof:<slug>"`. При нажатии Hermes получает callback и выполняет:

```
/model gpt-5.5 --provider openai-codex
```

После нажатия рекомендуется `/new` для сброса контекста.

### Autoswitch (автопереключение)

codex-profile-manager.py умеет автоматически переключать профиль, если активный достиг 95% по любому окну:

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
chmod 700 ~/.local/bin/codex-profile-manager.py
chmod 700 ~/.local/bin/send_buttons.py
```

Add to `config.yaml` → `quick_commands` (see Russian section above).

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
│   └── send_buttons.py            # Hermes-native card sender
├── plugin/
│   ├── index.js                   # OpenClaw plugin bridge (stub)
│   ├── openclaw.plugin.json        # Plugin manifest
│   └── package.json
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
| `/gptt` | Перейти на gpt-5.5 (Codex route) |
| `/mmfast` | Вернуться на MiniMax-M2.7 |
| `codex-profile-manager.py status` | CLI: показать статус всех профилей |
| `codex-profile-manager.py autoswitch` | CLI: автопереключение при исчерпании |
| `codex-profile-manager.py switch <slug>` | CLI: переключить на профиль |
| `codex-profile-manager.py refresh` | CLI: обновить cache usage |
