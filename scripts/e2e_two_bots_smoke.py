"""Real Telegram smoke runner for both production bot processes.

This is intentionally opt-in. It starts bot.py and courier_bot.py with the
same environment used in production, verifies both processes reach Telegram's
getMe endpoint, and optionally sends/deletes one smoke message per bot.

It does not fake Telegram callback queries. The full button-driven business
flow should be driven manually from the dedicated test Telegram accounts
after the two processes are confirmed healthy.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
import json

from dotenv import load_dotenv


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))


def telegram_request(token: str, method: str, **params):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=15) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = body
        raise RuntimeError(
            f"Telegram {method} failed with HTTP {exc.code}: {payload}"
        ) from exc

    if not payload.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {payload}")
    return payload["result"]


def required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def wait_for_bot(token: str, label: str, process: subprocess.Popen, timeout: float = 20):
    deadline = time.monotonic() + timeout
    last_error = None

    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"{label} process exited early with code {process.returncode}"
            )
        try:
            me = telegram_request(token, "getMe")
            print(f"[OK] {label}: @{me['username']} (id={me['id']})")
            return me
        except Exception as exc:
            last_error = exc
            time.sleep(1)

    raise RuntimeError(f"{label} did not become reachable: {last_error}")


def send_smoke(token: str, chat_id: str, label: str):
    message = telegram_request(
        token,
        "sendMessage",
        chat_id=chat_id,
        text=f"🧪 E2E smoke OK: {label}",
    )
    telegram_request(
        token,
        "deleteMessage",
        chat_id=chat_id,
        message_id=message["message_id"],
    )
    print(f"[OK] {label}: real Telegram send/delete succeeded")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--send-smoke",
        action="store_true",
        help="send and delete a real Telegram smoke message to configured test chats",
    )
    args = parser.parse_args()

    picker_token = required("BOT_TOKEN")
    courier_token = required("COURIER_BOT_TOKEN")

    picker = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "bot.py")],
        cwd=ROOT,
        env=os.environ.copy(),
    )
    courier = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "courier_bot.py")],
        cwd=ROOT,
        env=os.environ.copy(),
    )

    try:
        wait_for_bot(picker_token, "picker bot", picker)
        wait_for_bot(courier_token, "courier bot", courier)

        if args.send_smoke:
            send_smoke(
                picker_token,
                required("E2E_PICKER_CHAT_ID"),
                "picker bot",
            )
            send_smoke(
                courier_token,
                required("E2E_COURIER_CHAT_ID"),
                "courier bot",
            )

        print()
        print("Both bot processes are running and reachable through Telegram.")
        print()
        print("Manual production E2E sequence:")
        print("  1. Register picker and courier test accounts.")
        print("  2. Approve both from the admin account.")
        print("  3. Start picker and courier shifts.")
        print("  4. Create/seed one new order.")
        print("  5. Picker starts assembly and completes it.")
        print("  6. Confirm courier receives the waiting-order notification.")
        print("  7. Courier picks up and delivers the order.")
        print("  8. Repeat with a rejected order and resolve it from admin.")
        print("  9. Repeat with two orders and verify strict oldest-first delivery.")
        return 0
    finally:
        for process in (courier, picker):
            if process.poll() is None:
                process.terminate()

        for process in (courier, picker):
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
