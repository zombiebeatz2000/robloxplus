from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from playwright.sync_api import BrowserContext, TimeoutError as PlaywrightTimeoutError, sync_playwright

ROOT_DIR = Path(__file__).resolve().parent
ENV_PATH = ROOT_DIR / ".env"
PROFILE_DIR = ROOT_DIR / ".roblox_plus_browser"
COOKIE_KEY = "ROBLOX_SECURITY_COOKIE"

ROBLOX_HOME_URL = "https://www.roblox.com/home"
ROBLOX_LOGIN_URL = "https://www.roblox.com/login"
AUTHENTICATED_USER_URL = "https://users.roblox.com/v1/users/authenticated"
USERNAME_RESOLVE_URL = "https://users.roblox.com/v1/usernames/users"
PROFILE_PLATFORM_URL = "https://apis.roblox.com/profile-platform-api/v1/profiles/get"

REQUEST_TIMEOUT = 25
LOGIN_TIMEOUT_SECONDS = 300


def _normalize_cookie(value: str | None) -> str | None:
    if not value:
        return None

    value = value.strip()
    if value.startswith(".ROBLOSECURITY="):
        value = value.split("=", 1)[1]

    return value or None


def _cookie_header(cookie: str) -> str:
    return f".ROBLOSECURITY={cookie}"


def _load_cookie() -> str | None:
    load_dotenv(ENV_PATH, override=True)
    return _normalize_cookie(os.getenv(COOKIE_KEY))


def _save_cookie(cookie: str) -> None:
    existing_lines: list[str] = []
    if ENV_PATH.exists():
        existing_lines = ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()

    replacement = f"{COOKIE_KEY}={cookie}"
    output: list[str] = []
    replaced = False

    for line in existing_lines:
        stripped = line.strip()
        if stripped.startswith(f"{COOKIE_KEY}="):
            if not replaced:
                output.append(replacement)
                replaced = True
            continue
        output.append(line)

    if not replaced:
        if output and output[-1].strip():
            output.append("")
        output.append(replacement)

    ENV_PATH.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")

    try:
        ENV_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def _session(cookie: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Cookie": _cookie_header(cookie),
        }
    )
    return session


def _validate_cookie(cookie: str) -> bool:
    try:
        response = _session(cookie).get(
            AUTHENTICATED_USER_URL,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException:
        return False

    return response.status_code == 200


def _find_security_cookie(context: BrowserContext) -> str | None:
    for item in context.cookies():
        if item.get("name") == ".ROBLOSECURITY":
            return _normalize_cookie(str(item.get("value") or ""))
    return None


def login_and_save_cookie() -> str:
    print("Opening Roblox login in Chromium...")
    print("Log in normally. This tool never prints your security cookie.")

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 820},
        )

        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(ROBLOX_LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)

            deadline = time.monotonic() + LOGIN_TIMEOUT_SECONDS
            cookie: str | None = None

            while time.monotonic() < deadline:
                cookie = _find_security_cookie(context)
                if cookie and _validate_cookie(cookie):
                    break
                page.wait_for_timeout(1000)

            if not cookie or not _validate_cookie(cookie):
                raise RuntimeError(
                    "Login was not completed within 5 minutes, or Roblox did not issue a valid cookie."
                )

            _save_cookie(cookie)
            print(f"Login saved to: {ENV_PATH}")
            return cookie
        finally:
            context.close()


def _resolve_username(session: requests.Session, username: str) -> tuple[str, int]:
    response = session.post(
        USERNAME_RESOLVE_URL,
        json={
            "usernames": [username],
            "excludeBannedUsers": False,
        },
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code != 200:
        raise RuntimeError(f"Username lookup failed: HTTP {response.status_code}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Username lookup returned invalid JSON.") from exc

    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list) or not entries:
        raise RuntimeError(f"Roblox user not found: {username}")

    entry = entries[0]
    if not isinstance(entry, dict) or entry.get("id") is None:
        raise RuntimeError(f"Roblox user not found: {username}")

    resolved_name = str(entry.get("name") or username)
    return resolved_name, int(entry["id"])


def _profile_request(
    session: requests.Session,
    user_id: int,
) -> requests.Response:
    payload = {
        "profileId": str(user_id),
        "profileType": "User",
        "components": [{"component": "UserProfileHeader"}],
        "includeComponentOrdering": True,
    }

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://www.roblox.com",
        "Referer": f"https://www.roblox.com/users/{user_id}/profile",
    }

    response = session.post(
        PROFILE_PLATFORM_URL,
        headers=headers,
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code == 403:
        csrf_token = response.headers.get("x-csrf-token")
        if csrf_token:
            headers["x-csrf-token"] = csrf_token
            response = session.post(
                PROFILE_PLATFORM_URL,
                headers=headers,
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )

    return response


def _extract_plus_field(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return None

    components = payload.get("components")
    if not isinstance(components, dict):
        return None

    header = components.get("UserProfileHeader")
    if not isinstance(header, dict):
        return None

    return header.get("isRobloxPlus")


def check_user(username: str, cookie: str) -> int:
    session = _session(cookie)
    resolved_name, user_id = _resolve_username(session, username)
    response = _profile_request(session, user_id)

    try:
        payload = response.json()
    except ValueError:
        payload = None

    raw_plus = _extract_plus_field(payload)
    plus_enabled = raw_plus is True

    print(f"user: @{resolved_name}")
    print(f"userid: {user_id}")
    print(f"roblox plus: {'yes' if plus_enabled else 'no'}")
    print(f"raw plus field: {json.dumps(raw_plus)}")
    print(f"HTTPS status: {response.status_code}")

    if response.status_code != 200:
        return 1

    if raw_plus is None:
        print("warning: Roblox returned no isRobloxPlus field.", file=sys.stderr)

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check whether a Roblox user has Roblox Plus.",
    )
    parser.add_argument(
        "username",
        nargs="?",
        help="Roblox username to check.",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Open Roblox login and refresh the saved .ROBLOSECURITY cookie.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        cookie = _load_cookie()

        if args.login:
            cookie = login_and_save_cookie()
            if not args.username:
                return 0

        if not cookie or not _validate_cookie(cookie):
            print("No valid saved Roblox login found.")
            cookie = login_and_save_cookie()

        if not args.username:
            print("Usage: py main.py username")
            print("Login only: py main.py --login")
            return 0

        username = args.username.strip().removeprefix("@")
        if not username:
            raise RuntimeError("A Roblox username is required.")

        return check_user(username, cookie)

    except PlaywrightTimeoutError as exc:
        print(f"Browser timed out: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"Network request failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
