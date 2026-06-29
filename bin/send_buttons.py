#!/usr/bin/env python3
"""
Public GPT profile card for Hermes.

Shows Codex route, active profile, auth/usage/cache metadata and inline buttons.
The Telegram callback handler lives in the Hermes gateway deployment.
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import os
import subprocess
import time
from typing import Any

import aiohttp
import yaml

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TARGET_CHAT_ID = os.getenv("GPTPROF_CHAT_ID", "")  # e.g. "123456789"
AUTH_PATH = os.getenv("HERMES_AUTH", "/home/hermes/.hermes/auth.json")
CONFIG_PATH = os.getenv("HERMES_CONFIG", "/home/hermes/.hermes/config.yaml")
HCP_DIR = os.path.expanduser(os.getenv("HERMES_HCP", "~/.hermes/gptprof/profiles"))
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
USAGE_TIMEOUT = 8
CACHE_MAX_AGE = 15 * 60
CACHE_PATH = "/tmp/gptprof_usage_cache.json"
CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
ACCESS_REFRESH_SKEW = int(os.getenv("GPTPROF_ACCESS_REFRESH_SKEW", str(48 * 60 * 60)))
FORCE_REFRESH = os.getenv("GPTPROF_FORCE_REFRESH", "0") == "1"
# Optional external OpenClaw import is a break-glass path, not the primary refresh path.
INTEL64_OPENCLAW_SYNC = os.getenv("GPTPROF_INTEL64_OPENCLAW_SYNC", "0") == "1"
INTEL64_SSH_TARGET = os.getenv("GPTPROF_INTEL64_SSH_TARGET", "")
INTEL64_OPENCLAW_PROFILES = os.getenv("GPTPROF_INTEL64_OPENCLAW_PROFILES", "~/.openclaw/codex-profiles")

PROFILES = [
    ("profile1", "Pro", "gpt-5.5"),
    ("profile2", "Pro", "gpt-5.5"),
    ("profile3", "Plus", "gpt-5.4-mini"),
]
DEFAULT_PROFILE_SLUGS = [slug for slug, _plan, _model in PROFILES]

DISPLAY_PRICE = {
    "profile1": "Pro",
    "profile2": "Pro",
    "profile3": "Plus",
}

DEFAULT_PLAN = {
    "profile1": "Pro",
    "profile2": "Pro",
    "profile3": "Plus",
}

PLAN_LABELS = {}


def load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, data: Any) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
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


def access_token_exp(token: str) -> float:
    exp = _jwt_payload(token).get("exp")
    return float(exp) if isinstance(exp, (int, float)) else 0.0


def access_token_expiring(token: str, skew: int = ACCESS_REFRESH_SKEW) -> bool:
    exp = access_token_exp(token)
    return not exp or exp <= time.time() + skew


def token_expiry_date(token: str) -> str:
    exp = access_token_exp(token)
    if not exp:
        return "unknown"
    try:
        return dt.datetime.fromtimestamp(exp, tz=dt.timezone.utc).date().isoformat()
    except Exception:
        return "unknown"


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


def save_profile(slug: str, data: dict[str, Any]) -> None:
    save_json(os.path.join(HCP_DIR, f"{slug}.json"), data)


def profile_catalog(profiles: dict[str, dict[str, Any]]) -> list[tuple[str, str, str]]:
    seen: set[str] = set()
    items: list[tuple[str, str, str]] = []
    for slug, default_plan, model in PROFILES:
        data = profiles.get(slug) or {}
        plan = normalize_plan(str(data.get("plan") or default_plan))
        items.append((slug, plan, model))
        seen.add(slug)
    for slug in sorted(profiles):
        if slug in seen:
            continue
        data = profiles.get(slug) or {}
        plan = normalize_plan(str(data.get("plan") or DEFAULT_PLAN.get(slug) or "OpenAI"))
        model = str(data.get("model") or "gpt-5.5")
        items.append((slug, plan, model))
    return items


def normalize_plan(plan: str) -> str:
    return PLAN_LABELS.get(plan.strip().lower(), plan)


def get_current_profile() -> str | None:
    auth = load_json(AUTH_PATH, {})
    if isinstance(auth, dict):
        codex = auth.get("codex") or {}
        if isinstance(codex, dict):
            return codex.get("profile")
    return None


def sync_active_auth(slug: str, profile: dict[str, Any]) -> None:
    """Keep Hermes runtime auth in sync when the active gptprof token rotates."""
    auth = load_json(AUTH_PATH, {})
    if not isinstance(auth, dict):
        return

    codex = auth.get("codex")
    active_slug = codex.get("profile") if isinstance(codex, dict) else None
    if isinstance(codex, dict) and active_slug == slug:
        codex["access_token"] = profile.get("access_token")
        codex["refresh_token"] = profile.get("refresh_token")
        codex["plan"] = profile.get("plan") or codex.get("plan")
        codex["email"] = profile.get("email") or codex.get("email")

        providers = auth.setdefault("providers", {})
        provider_state = providers.setdefault("openai-codex", {})
        provider_tokens = dict(provider_state.get("tokens") or {})
        provider_tokens.update({
            "profile": slug,
            "email": profile.get("email"),
            "plan": profile.get("plan"),
            "access_token": profile.get("access_token"),
            "refresh_token": profile.get("refresh_token"),
        })
        provider_state["tokens"] = provider_tokens
        provider_state["auth_mode"] = "chatgpt"
        provider_state.pop("last_auth_error", None)
        auth["active_provider"] = "openai-codex"

    pool_root = auth.setdefault("credential_pool", {})
    pool = pool_root.get("openai-codex")
    if isinstance(pool, list):
        source = f"gptprof:{slug}"
        selected_entry = {
            "source": source,
            "profile": slug,
            "label": slug,
            "provider": "openai-codex",
            "email": profile.get("email"),
            "plan": profile.get("plan"),
            "access_token": profile.get("access_token"),
            "refresh_token": profile.get("refresh_token"),
            "priority": 0,
            "last_status": "ok",
            "last_status_at": time.time(),
        }
        remaining_pool = []
        for item in pool:
            if not isinstance(item, dict):
                continue
            item_source = str(item.get("source") or "")
            item_profile = str(item.get("profile") or item.get("label") or "")
            if item_source in {source, "device_code"} or item_profile == slug:
                continue
            if item.get("priority") == 0:
                item = {**item, "priority": 10}
            remaining_pool.append(item)
        pool_root["openai-codex"] = [selected_entry, *remaining_pool]

    save_json(AUTH_PATH, auth)


def sync_from_intel64_openclaw(
    profiles: dict[str, dict[str, Any]],
    cache: dict[str, Any],
    only_slugs: list[str] | None = None,
    force: bool = False,
) -> list[str]:
    """Import OpenClaw profile tokens from an optional external host, if reachable."""
    if not INTEL64_OPENCLAW_SYNC and not force:
        return []
    if not INTEL64_SSH_TARGET:
        return []
    wanted = set(only_slugs or [])
    slugs = [slug for slug, _plan, _model in PROFILES if not wanted or slug in wanted]
    if not slugs:
        return []
    script = f"""
import json
from pathlib import Path
base = Path({os.path.expanduser(INTEL64_OPENCLAW_PROFILES)!r})
out = {{}}
for slug in {slugs!r}:
    p = base / slug / 'auth.json'
    try:
        out[slug] = json.loads(p.read_text())
    except Exception as exc:
        out[slug] = {{'_error': type(exc).__name__}}
print(json.dumps(out))
""".strip()
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", INTEL64_SSH_TARGET, f"python3 - <<'PY'\n{script}\nPY"],
            check=False,
            capture_output=True,
            text=True,
            timeout=12,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        remote_profiles = json.loads(result.stdout)
    except Exception:
        return []

    updated: list[str] = []
    for slug in slugs:
        remote = remote_profiles.get(slug)
        if not isinstance(remote, dict) or remote.get("_error"):
            continue
        tokens = remote.get("tokens") if isinstance(remote.get("tokens"), dict) else remote
        access_token = tokens.get("access_token") or remote.get("accessToken")
        refresh_token = tokens.get("refresh_token") or remote.get("refreshToken")
        if not isinstance(access_token, str) or not isinstance(refresh_token, str):
            continue
        local = profiles.get(slug) or {}
        local_exp = access_token_exp(str(local.get("access_token") or ""))
        remote_exp = access_token_exp(access_token)
        # Pull when intel64 has a fresher token or local token is absent/expiring.
        if remote_exp <= local_exp + 60 and not access_token_expiring(str(local.get("access_token") or "")):
            continue
        local.update({
            "profile": slug,
            "email": remote.get("email") or local.get("email") or f"{slug}@gmail.com",
            "plan": local.get("plan") or DEFAULT_PLAN.get(slug, "Codex"),
            "access_token": access_token,
            "refresh_token": refresh_token,
            "last_refresh": remote.get("last_refresh") or local.get("last_refresh"),
            "source": "intel64-openclaw",
            "synced_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        })
        local.pop("_refresh_error", None)
        profiles[slug] = local
        save_profile(slug, local)
        sync_active_auth(slug, local)
        cache.pop(slug, None)
        updated.append(slug)
    return updated


async def refresh_profile_token(session: aiohttp.ClientSession, slug: str, profile: dict[str, Any]) -> tuple[bool, str | None]:
    """Refresh an expired/near-expired Codex access token for usage checks."""
    access_token = str(profile.get("access_token") or "")
    refresh_token = str(profile.get("refresh_token") or "")
    if not refresh_token:
        return False, "refresh missing"
    if not FORCE_REFRESH and not access_token_expiring(access_token):
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
    profile["source"] = "hermes-local-refresh"
    profile.pop("_refresh_error", None)
    save_profile(slug, profile)
    sync_active_auth(slug, profile)
    return True, None


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
    seconds = (
        window.get("seconds_until_reset")
        or window.get("reset_after_seconds")
        or window.get("reset_in_seconds")
    )
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


def reset_text(reset_at: float | None, usage_error: str | None = None) -> str:
    if isinstance(reset_at, (int, float)):
        return format_duration(float(reset_at) - time.time())
    if usage_error:
        if "token_revoked" in usage_error:
            return "token revoked · new auth needed"
        if "token_expired" in usage_error:
            return "token expired · new auth needed"
        return f"unavailable · {usage_error}"
    return "unknown"



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
            parsed = parse_usage(cached)
            try:
                err_payload = await r.json(content_type=None)
                err = err_payload.get("error") if isinstance(err_payload, dict) else None
                code = err.get("code") if isinstance(err, dict) else None
                message = err.get("message") if isinstance(err, dict) else None
                if code:
                    parsed["usage_error"] = str(code)
                elif message:
                    parsed["usage_error"] = str(message)[:80]
                else:
                    parsed["usage_error"] = f"usage {r.status}"
            except Exception:
                parsed["usage_error"] = f"usage {r.status}"
            return slug, parsed
    except Exception as exc:
        parsed = parse_usage(cached)
        parsed["usage_error"] = f"usage {type(exc).__name__}"
        return slug, parsed
    return slug, parse_usage(cached)


def dollar_label(slug: str, plan: str) -> str:
    plan = normalize_plan(plan)
    if "$" in plan:
        return plan.split("$", 1)[-1].join(["$", ""])
    if plan:
        return plan
    return DISPLAY_PRICE.get(slug) or plan


def pct_text(value: int | None) -> str:
    return "—" if value is None else f"{value}%"


def profile_block(slug: str, plan: str, data: dict[str, Any], usage: dict[str, Any], active: bool) -> str:
    marker = "✅" if active else "▪️"
    status = " · active" if active else ""
    plan = normalize_plan(plan)
    refresh_error = data.get("_refresh_error")
    if refresh_error == "refresh_token_reused":
        refresh = "refresh reused · new auth needed"
    elif refresh_error:
        refresh = f"refresh failed · {refresh_error}"
    else:
        refresh = "refresh ok" if data.get("refresh_token") else "refresh missing"
    expiry = token_expiry_date(str(data.get("access_token") or ""))
    primary_left = usage.get("primary_left")
    secondary_left = usage.get("secondary_left")
    usage_error = str(usage.get("usage_error") or "") or None
    primary_reset = reset_text(usage.get("primary_reset"), usage_error)
    secondary_reset = reset_text(usage.get("secondary_reset"), usage_error)
    lines = [
        f"{marker} {slug} [{dollar_label(slug, plan)}]{status}",
        f"🔐 {refresh} · expires {expiry}",
        f"📊 5h: {pct_text(primary_left)} left · reset {primary_reset}",
        f"📅 Week: {pct_text(secondary_left)} left · reset {secondary_reset}",
        f"🕒 Cache: {usage.get('cache') or 'unknown'}",
    ]
    if usage.get("usage_error"):
        lines.append(f"⚠️ Usage API: {usage['usage_error']}")
    return "\n".join(lines)


def route_model_label(model: str) -> str:
    if model.startswith("openai/"):
        return model
    return f"openai/{model}" if model.startswith(("gpt-", "o")) else model


async def main() -> None:
    from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

    bot = Bot(token=TOKEN)
    profiles = load_profiles()
    catalog = profile_catalog(profiles)
    current_slug = get_current_profile() or (catalog[0][0] if catalog else "")
    current_model = get_current_model()

    cache = load_cache()
    sync_from_intel64_openclaw(profiles, cache)
    refresh_errors: dict[str, str] = {}
    connector = aiohttp.TCPConnector(limit=6, force_close=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        for slug, _plan, _model in catalog:
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
        for slug, _plan, _model in catalog:
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
    for slug, plan, _model in catalog:
        blocks.append(profile_block(slug, plan, profiles.get(slug, {}), usage_map.get(slug, {}), slug == current_slug))
    text = "\n\n".join(["\n".join(lines).rstrip(), *blocks])

    profile_buttons = []
    for slug, plan, model in catalog:
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
        InlineKeyboardButton("✅ Check auth", callback_data="gptprof:check_auth"),
    ])
    rows.append([InlineKeyboardButton("⤴ Back to Pi route", callback_data="gptprof:pi_route")])
    keyboard = InlineKeyboardMarkup(rows)

    await bot.send_message(
        chat_id=int(TARGET_CHAT_ID),
        text=text,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )
    if os.getenv("GPTPROF_VERBOSE") == "1":
        print("sent")


if __name__ == "__main__":
    asyncio.run(main())
