#!/usr/bin/env python3
"""
send_buttons.py — Hermes-native GPT profile card with inline buttons.
Works with any Hermes setup that has:
  - TELEGRAM_BOT_TOKEN env var
  - auth.json with active Codex profile
  - config.yaml with model settings
  - hcp/ directory with profile JSON files

No tokens or secrets are stored in this script.
"""
import asyncio
import os
import json
import time
import aiohttp
import yaml
from pathlib import Path

# ── Config (override via env) ────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHIP_DM            = os.getenv("CHIP_DM", "")          # e.g. "617744661"
AUTH_PATH          = os.getenv("HERMES_AUTH", "/home/hermes/.hermes/auth.json")
CONFIG_PATH        = os.getenv("HERMES_CONFIG", "/home/hermes/.hermes/config.yaml")
HCP_DIR            = os.getenv("HERMES_HCP", "/home/hermes/.hermes/skills/chip/hcp")
USAGE_URL          = "https://chatgpt.com/backend-api/wham/usage"
USAGE_TIMEOUT      = 8
CACHE_MAX_AGE      = 15 * 60   # seconds

PROFILES = [
    ("gptinvest23",  "Pro $200",    "🤖"),
    ("markov495",    "ProLite $100","⚡"),
    ("mintsage",     "Plus $20",    "✨"),
    ("omnifocusme",  "Plus $20",    "🎯"),
]
PROFILE_EMOJI = {slug: emoji for slug, _, emoji in PROFILES}

_CACHE = "/tmp/gptprof_usage_cache.json"


def load_cache():
    try:
        with open(_CACHE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(data):
    try:
        with open(_CACHE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def parse_usage(payload):
    """Return (primary_rem_pct, secondary_rem_pct) from wham payload."""
    if not payload:
        return None, None
    rl = payload.get("rate_limit") or {}
    pw = rl.get("primary_window") or {}
    sw = rl.get("secondary_window") or {}
    pu = pw.get("used_percent")
    su = sw.get("used_percent")
    p = max(0, 100 - int(pu)) if isinstance(pu, (int, float)) else None
    s = max(0, 100 - int(su)) if isinstance(su, (int, float)) else None
    return p, s


def pct(p):
    return f"{p}%" if p is not None else "—"


async def fetch_usage(session, token, slug):
    """Fetch usage for one profile. Returns (slug, primary_rem, secondary_rem, label_str)."""
    cache = load_cache()
    cached = cache.get(slug, {})
    age = time.time() - cached.get("_fetched", 0)
    if age < CACHE_MAX_AGE and "primary_window" in cached:
        p, s = parse_usage(cached)
        return slug, p, s, f"5ч:{pct(p)} нед:{pct(s)}"

    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://chatgpt.com/",
    }
    try:
        async with session.get(
            USAGE_URL, headers=headers,
            timeout=aiohttp.ClientTimeout(total=USAGE_TIMEOUT)
        ) as r:
            if r.status == 200:
                data = await r.json()
                data["_fetched"] = time.time()
                cache[slug] = data
                save_cache(cache)
                p, s = parse_usage(data)
                return slug, p, s, f"5ч:{pct(p)} нед:{pct(s)}"
    except Exception:
        pass

    # fallback to stale cache
    if "primary_window" in cached:
        p, s = parse_usage(cached)
        return slug, p, s, f"5ч:{pct(p)} нед:{pct(s)}"
    return slug, None, None, "—"


def get_current_profile():
    """Read active profile slug from Hermes auth.json."""
    try:
        with open(AUTH_PATH) as f:
            auth = json.load(f)
        return (auth.get("codex") or {}).get("profile")
    except Exception:
        return None


def get_current_model():
    """Read current model from Hermes config.yaml."""
    try:
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        m = cfg.get("model", "minimax")
        if isinstance(m, dict):
            m = m.get("default") or m.get("model") or "minimax"
        return str(m)
    except Exception:
        return "minimax"


async def main():
    if not TELEGRAM_BOT_TOKEN or not CHIP_DM:
        print("ERROR: TELEGRAM_BOT_TOKEN and CHIP_DM must be set")
        return

    from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    current_slug = get_current_profile()
    current_model = get_current_model()

    # load tokens from hcp dir
    tokens = {}
    hcp = Path(HCP_DIR)
    if hcp.is_dir():
        for f in hcp.glob("*.json"):
            try:
                d = json.loads(f.read_text())
                tok = d.get("access_token", "")
                if tok:
                    tokens[f.stem] = tok
            except Exception:
                pass

    # fetch all in parallel
    connector = aiohttp.TCPConnector(limit=6, force_close=True)
    async with aiohttp.ClientSession(connector=connector) as sess:
        results = await asyncio.gather(*[
            fetch_usage(sess, tok, slug) for slug, tok in tokens.items()
        ])

    usage = {slug: (p, s, lbl) for slug, p, s, lbl in results}

    # header
    cur_emoji = PROFILE_EMOJI.get(current_slug, "🤖")
    cur_plan  = next((p[1] for p in PROFILES if p[0] == current_slug), "")
    cur_lbl   = usage.get(current_slug, (None, None, "—"))[2]
    header = f"{cur_emoji} *{current_slug}* ({cur_plan}) — {cur_lbl}"

    # buttons
    rows = []
    for slug, plan, emoji in PROFILES:
        p, s, lbl = usage.get(slug, (None, None, "—"))
        parts = [emoji]
        if slug == current_slug:
            parts.insert(0, "✓")
        parts.extend([slug, f"({lbl})"])
        rows.append([InlineKeyboardButton(
            " ".join(parts),
            callback_data=f"gptprof:{slug}"
        )])

    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="gptprof:cancel")])

    text = (
        "*🤖 GPT Profile*\n\n"
        f"{header}\n"
        f"`📦 {current_model}`\n\n"
        "Выбери профиль:\n\n"
        "_(После выбора нажми /new)_"
    )

    await bot.send_message(
        chat_id=int(CHIP_DM),
        text=text,
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="Markdown",
    )
    print("OK")


if __name__ == "__main__":
    asyncio.run(main())
