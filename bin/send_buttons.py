#!/usr/bin/env python3
"""
OpenClaw-style GPT profile card for Chip.

Shows Codex route, active profile, auth/usage/cache metadata and inline buttons.
The Telegram callback handler lives in /opt/hermes-agent/gateway/platforms/telegram.py.
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import os
import time
from typing import Any

import aiohttp
import yaml

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHIP_DM = os.getenv("CHIP_DM", "")  # e.g. "123456789"
AUTH_PATH = os.getenv("HERMES_AUTH", "/home/hermes/.hermes/auth.json")
CONFIG_PATH = os.getenv("HERMES_CONFIG", "/home/hermes/.hermes/config.yaml")
HCP_DIR = os.getenv("HERMES_HCP", "/home/hermes/.hermes/skills/chip/hcp")
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
USAGE_TIMEOUT = 8
CACHE_MAX_AGE = 15 * 60
CACHE_PATH = "/tmp/gptprof_usage_cache.json"
CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
ACCESS_REFRESH_SKEW = 120

# Keep callback models unchanged; only the visible labels mimic OpenClaw.
PROFILES = [
    ("gptinvest23", "Pro $200", "gpt-5.5"),
    ("markov495", "ProLite $100", "gpt-5.4"),
    ("mintsage", "Plus $20", "gpt-5.4-mini"),
    ("omnifocusme", "Plus $20", "gpt-5.4-mini"),
]

# OpenClaw display uses compact dollar labels. markov495 is shown as [$200] there.
DISPLAY_PRICE = {
    "gptinvest23": "$200",
    "markov495": "$200",
    "mintsage": "$20",
    "omnifocusme": "$20",
}


def load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, data: Any) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def load_cache() -> dict[str, Any]:
    data = load_json(CACHE_PATH, {})
    return data if isinstance(data, dict) else {}


def save_cache(data: dict[str, Any]) -> None:
    save_json(CACHE_PATH, data)


def _jwt_payload(token: str) -> dict[str, Any]:
    try:
        part = token.split(".")[1]
        part += "=" * ((4 - len(part) % 4) % 4)
        return json.loads(base64.urlsafe_b64decode(part.encode()))
    except Exception:
        return {}


def token_expiry_date(token: str) -> str:
    exp = _jwt_payload(token).get("exp")
    if not isinstance(exp, (int, float)):
        return "unknown"
    try:
        return dt.datetime.fromtimestamp(exp, tz=dt.timezone.utc).date().isoformat()
    except Exception:
        return "unknown"


def access_token_expiring(token: str, skew: int = ACCESS_REFRESH_SKEW) -> bool:
    exp = _jwt_payload(token).get("exp")
    if not isinstance(exp, (int, float)):
        return True
    return float(exp) <= time.time() + skew


def save_profile(slug: str, data: dict[str, Any]) -> None:
    save_json(os.path.join(HCP_DIR, f"{slug}.json"), data)


def sync_active_auth(slug: str, profile: dict[str, Any]) -> None:
    """Keep Hermes runtime auth in sync when the active gptprof token rotates."""
    auth = load_json(AUTH_PATH, {})
    if not isinstance(auth, dict):
        return
    codex = auth.get("codex")
    if isinstance(codex, dict) and codex.get("profile") == slug:
        codex["access_token"] = profile.get("access_token")
        codex["refresh_token"] = profile.get("refresh_token")
        codex.setdefault("plan", profile.get("plan"))
        codex.setdefault("email", profile.get("email"))
    pool_root = auth.get("credential_pool")
    if isinstance(pool_root, dict):
        pool = pool_root.get("openai-codex")
        if isinstance(pool, list):
            for item in pool:
                if isinstance(item, dict) and item.get("source") == f"gptprof:{slug}":
                    item["access_token"] = profile.get("access_token")
                    item["refresh_token"] = profile.get("refresh_token")
                    item["last_status"] = "ok"
                    item["last_status_at"] = time.time()
    save_json(AUTH_PATH, auth)


async def refresh_profile_token(session: aiohttp.ClientSession, slug: str, profile: dict[str, Any]) -> tuple[bool, str | None]:
    """Refresh an expired/near-expired Codex access token for usage checks."""
    access_token = str(profile.get("access_token") or "")
    refresh_token = str(profile.get("refresh_token") or "")
    if not refresh_token or not access_token_expiring(access_token):
        return False, None
    try:
        async with session.post(
            CODEX_OAUTH_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CODEX_OAUTH_CLIENT_ID,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            timeout=aiohttp.ClientTimeout(total=USAGE_TIMEOUT),
        ) as r:
            if r.status != 200:
                try:
                    err_payload = await r.json()
                    err_obj = err_payload.get("error") if isinstance(err_payload, dict) else None
                    code = err_obj.get("code") if isinstance(err_obj, dict) else err_payload.get("error") if isinstance(err_payload, dict) else None
                    if code:
                        return False, str(code)
                except Exception:
                    pass
                return False, f"refresh {r.status}"
            payload = await r.json()
    except Exception as exc:
        return False, f"refresh {type(exc).__name__}"

    new_access = payload.get("access_token")
    if not isinstance(new_access, str) or not new_access.strip():
        return False, "refresh missing access"
    profile["access_token"] = new_access.strip()
    new_refresh = payload.get("refresh_token")
    if isinstance(new_refresh, str) and new_refresh.strip():
        profile["refresh_token"] = new_refresh.strip()
    profile["last_refresh"] = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    save_profile(slug, profile)
    sync_active_auth(slug, profile)
    return True, None


def load_profiles() -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    try:
        names = sorted(os.listdir(HCP_DIR))
    except Exception:
        names = []
    for fname in names:
        if not fname.endswith(".json"):
            continue
        slug = fname[:-5]
        data = load_json(os.path.join(HCP_DIR, fname), {})
        if isinstance(data, dict):
            profiles[slug] = data
    return profiles


def get_current_profile() -> str | None:
    auth = load_json(AUTH_PATH, {})
    if isinstance(auth, dict):
        codex = auth.get("codex") or {}
        if isinstance(codex, dict):
            return codex.get("profile")
    return None


def get_current_model() -> str:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        model = cfg.get("model", "minimax")
        if isinstance(model, dict):
            model = model.get("default") or model.get("model") or "minimax"
        return str(model)
    except Exception:
        return "minimax"


def pct_left(window: dict[str, Any] | None) -> int | None:
    if not isinstance(window, dict):
        return None
    used = window.get("used_percent")
    if not isinstance(used, (int, float)):
        return None
    return max(0, min(100, 100 - int(used)))


def window_reset_at(window: dict[str, Any] | None) -> float | None:
    if not isinstance(window, dict):
        return None
    reset_at = window.get("reset_at") or window.get("resets_at")
    if isinstance(reset_at, (int, float)):
        return float(reset_at)
    seconds = window.get("seconds_until_reset")
    if isinstance(seconds, (int, float)):
        return time.time() + float(seconds)
    return None


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    seconds = int(seconds)
    if seconds <= 0:
        return "expired"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m" if minutes else "<1m"


def cache_label(fetched_at: float | None) -> str:
    if not fetched_at:
        return "unknown"
    age = max(0, time.time() - float(fetched_at))
    if age < 10:
        label = "just now"
    elif age < 60:
        label = f"{int(age)}s ago"
    elif age < 3600:
        label = f"{int(age // 60)}m ago"
    else:
        label = f"{int(age // 3600)}h ago"
    if age > CACHE_MAX_AGE:
        label += " · stale"
    return label


def parse_usage(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = payload or {}
    rl = payload.get("rate_limit") or {}
    primary = rl.get("primary_window") or payload.get("primary_window") or {}
    secondary = rl.get("secondary_window") or payload.get("secondary_window") or {}
    p = pct_left(primary)
    s = pct_left(secondary)
    p_reset = window_reset_at(primary)
    s_reset = window_reset_at(secondary)
    fetched = payload.get("_fetched")
    return {
        "primary_left": p,
        "secondary_left": s,
        "primary_reset": p_reset,
        "secondary_reset": s_reset,
        "cache": cache_label(fetched if isinstance(fetched, (int, float)) else None),
    }


async def fetch_usage(session: aiohttp.ClientSession, token: str, slug: str, cache: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    cached = cache.get(slug, {}) if isinstance(cache.get(slug), dict) else {}
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://chatgpt.com/",
    }
    try:
        async with session.get(USAGE_URL, headers=headers, timeout=aiohttp.ClientTimeout(total=USAGE_TIMEOUT)) as r:
            if r.status == 200:
                data = await r.json()
                data["_fetched"] = time.time()
                cache[slug] = data
                return slug, parse_usage(data)
    except Exception:
        pass
    return slug, parse_usage(cached)


def dollar_label(slug: str, plan: str) -> str:
    return DISPLAY_PRICE.get(slug) or (plan.split("$", 1)[-1].join(["$", ""]) if "$" in plan else plan)


def pct_text(value: int | None) -> str:
    return "—" if value is None else f"{value}%"


def profile_block(slug: str, plan: str, data: dict[str, Any], usage: dict[str, Any], active: bool) -> str:
    marker = "✅" if active else "▪️"
    status = " · active" if active else ""
    refresh_error = data.get("_refresh_error")
    if refresh_error == "refresh_token_reused":
        refresh = "refresh reused · new auth needed"
    elif refresh_error:
        refresh = f"refresh failed · {refresh_error}"
    else:
        refresh = "refresh ok" if data.get("refresh_token") else "refresh missing"
    expiry = token_expiry_date(str(data.get("access_token") or ""))
    primary_left = usage.get('primary_left')
    secondary_left = usage.get('secondary_left')
    primary_reset = "unknown" if primary_left is None else format_duration((usage.get('primary_reset') or 0) - time.time())
    secondary_reset = "unknown" if secondary_left is None else format_duration((usage.get('secondary_reset') or 0) - time.time())
    return "\n".join([
        f"{marker} {slug} [{dollar_label(slug, plan)}]{status}",
        f"🔐 {refresh} · expires {expiry}",
        f"📊 5h: {pct_text(primary_left)} left · reset {primary_reset}",
        f"📅 Week: {pct_text(secondary_left)} left · reset {secondary_reset}",
        f"🕒 Cache: {usage.get('cache') or 'unknown'}",
    ])


def route_model_label(model: str) -> str:
    if model.startswith("openai/"):
        return model
    return f"openai/{model}" if model.startswith(("gpt-", "o")) else model


async def main() -> None:
    from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

    bot = Bot(token=TOKEN)
    profiles = load_profiles()
    current_slug = get_current_profile() or PROFILES[0][0]
    current_model = get_current_model()

    cache = load_cache()
    connector = aiohttp.TCPConnector(limit=6, force_close=True)
    refresh_errors: dict[str, str] = {}
    async with aiohttp.ClientSession(connector=connector) as session:
        for slug, _plan, _model in PROFILES:
            profile = profiles.get(slug) or {}
            if profile.get("access_token"):
                refreshed, err = await refresh_profile_token(session, slug, profile)
                if refreshed:
                    profiles[slug] = profile
                    cache.pop(slug, None)
                elif err:
                    profile["_refresh_error"] = err
                    refresh_errors[slug] = err
        tasks = []
        for slug, _plan, _model in PROFILES:
            token = str((profiles.get(slug) or {}).get("access_token") or "")
            if token:
                tasks.append(fetch_usage(session, token, slug, cache))
        results = await asyncio.gather(*tasks) if tasks else []
    save_cache(cache)
    usage_map = {slug: usage for slug, usage in results}

    lines = [
        "✅ Autoswitch: no switch needed",
        f"🤖 GPT profile: {current_slug}",
        "🧪 Route: native Codex trial",
        f"🧠 Model: {route_model_label(current_model)}",
        "⚙️ Runtime: codex",
        f"↪️ Pi fallback available: openai-codex/{current_model} · runtime=pi",
        "",
    ]

    blocks = []
    for slug, plan, _model in PROFILES:
        blocks.append(profile_block(slug, plan, profiles.get(slug, {}), usage_map.get(slug, {}), slug == current_slug))
    text = "\n\n".join(["\n".join(lines).rstrip(), *blocks])

    profile_buttons = []
    for slug, plan, model in PROFILES:
        usage = usage_map.get(slug, {})
        week = usage.get("secondary_left")
        symbol = "✓" if slug == current_slug else ("⚠" if isinstance(week, int) and week <= 0 else "↔")
        btn_text = f"{symbol} {pct_text(week)} {slug} [{dollar_label(slug, plan)}]"
        profile_buttons.append(InlineKeyboardButton(btn_text, callback_data=f"gptprof:{slug}:{model}"))

    rows = [profile_buttons[i:i + 2] for i in range(0, len(profile_buttons), 2)]
    rows.append([
        InlineKeyboardButton("🔄 Usage", callback_data="gptprof:refresh"),
        InlineKeyboardButton("🔁 Autoswitch", callback_data="gptprof:autoswitch"),
    ])
    rows.append([
        InlineKeyboardButton("➕ New auth", callback_data="gptprof:new_auth"),
    ])
    rows.append([
        InlineKeyboardButton("⤴ Back to Pi route", callback_data="gptprof:pi_route"),
    ])
    keyboard = InlineKeyboardMarkup(rows)

    await bot.send_message(
        chat_id=int(CHIP_DM),
        text=text,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )
    print("OK")


if __name__ == "__main__":
    asyncio.run(main())
