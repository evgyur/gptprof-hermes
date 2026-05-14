#!/usr/bin/env python3
"""
Local Hermes gptprof token refresher.

Keeps Codex OAuth profiles alive without depending on OpenClaw as a canonical token source.
Safe to run from systemd/cron; writes no secrets to stdout.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import os
import sys
import time
from pathlib import Path
from typing import Any

parser = argparse.ArgumentParser(description="Refresh Hermes gptprof Codex OAuth profiles")
parser.add_argument("--force", action="store_true", help="refresh even when access tokens are not near expiry")
parser.add_argument("--json", action="store_true", help="emit machine-readable status JSON")
args = parser.parse_args()

# Must be set before importing send_buttons: it reads these envs at import time.
os.environ.setdefault("GPTPROF_INTEL64_OPENCLAW_SYNC", "0")
os.environ.setdefault("GPTPROF_ACCESS_REFRESH_SKEW", str(48 * 60 * 60))
if args.force:
    os.environ["GPTPROF_FORCE_REFRESH"] = "1"

import aiohttp  # noqa: E402

from send_buttons import (  # noqa: E402
    PROFILES,
    HCP_DIR,
    USAGE_TIMEOUT,
    access_token_exp,
    refresh_profile_token,
    save_profile,
    sync_active_auth,
    token_expiry_date,
    load_profiles,
)

LOCK_PATH = os.getenv("GPTPROF_REFRESH_LOCK", "/tmp/gptprof-token-refresh.lock")


def _status(slug: str, profile: dict[str, Any], state: str, detail: str | None = None) -> dict[str, Any]:
    return {
        "slug": slug,
        "state": state,
        "detail": detail,
        "expires": token_expiry_date(str(profile.get("access_token") or "")),
        "exp_ts": access_token_exp(str(profile.get("access_token") or "")),
    }


async def run() -> tuple[int, list[dict[str, Any]]]:
    profiles = load_profiles()
    results: list[dict[str, Any]] = []
    had_error = False

    connector = aiohttp.TCPConnector(limit=4, force_close=True)
    async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=USAGE_TIMEOUT + 4)) as session:
        for slug, _plan, _model in PROFILES:
            profile = profiles.get(slug) or {}
            if not profile:
                results.append(_status(slug, profile, "missing_profile", f"{HCP_DIR}/{slug}.json"))
                had_error = True
                continue
            if not profile.get("refresh_token"):
                profile["_refresh_error"] = "refresh missing"
                profile["last_refresh_error_at"] = time.time()
                save_profile(slug, profile)
                results.append(_status(slug, profile, "error", "refresh missing"))
                had_error = True
                continue

            refreshed, err = await refresh_profile_token(session, slug, profile)
            if refreshed:
                profile["last_refresh_manager"] = "hermes-systemd-timer"
                save_profile(slug, profile)
                sync_active_auth(slug, profile)
                results.append(_status(slug, profile, "refreshed"))
            elif err:
                profile["_refresh_error"] = err
                profile["last_refresh_error_at"] = time.time()
                save_profile(slug, profile)
                results.append(_status(slug, profile, "error", err))
                had_error = True
            else:
                results.append(_status(slug, profile, "fresh"))

    return (1 if had_error else 0), results


def print_results(results: list[dict[str, Any]]) -> None:
    if args.json:
        import json

        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    for item in results:
        detail = f" · {item['detail']}" if item.get("detail") else ""
        print(f"{item['slug']}: {item['state']} · expires {item['expires']}{detail}")


def main() -> int:
    Path(HCP_DIR).mkdir(parents=True, exist_ok=True)
    with open(LOCK_PATH, "w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("gptprof refresh already running")
            return 0
        exit_code, results = asyncio.run(run())
        print_results(results)
        with contextlib.suppress(Exception):
            fcntl.flock(lock, fcntl.LOCK_UN)
        return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
