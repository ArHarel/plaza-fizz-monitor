#!/usr/bin/env python3
"""
Persistent availability monitor for two student-housing sites in Utrecht:

  1. Plaza (newnewnew.space) — Limapad / Campus 030
     JS-rendered site -> needs Playwright (ASYNC API — Render's environment
     conflicts with Playwright's sync API, which needs a thread with no
     event loop at all; the async API avoids that by running under our own
     single asyncio event loop instead).

  2. THE FIZZ (the-fizz.com) — Utrecht building
     Plain server-rendered HTML -> plain HTTP request, no browser needed.

Runs forever in a loop (for hosting on an always-on free web service like
Render). Also starts a tiny HTTP server on $PORT so the host classifies this
as a "web service" and keeps it running; pair with a free uptime-pinger
(e.g. UptimeRobot) hitting that URL every 5 minutes so the free instance
never spins down from inactivity.

State is kept in memory (not git-committed) — fine for a long-running
process. If the process restarts, you'll get one baseline run with no alert,
same as before.

Env vars required:
  TELEGRAM_TOKEN
  TELEGRAM_CHAT_ID

Optional:
  CHECK_INTERVAL_SECONDS   default 20
  PORT                     default 10000 (Render sets this automatically)
"""
import asyncio
import json
import os
import threading
import time
import traceback
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from playwright.async_api import async_playwright

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "20"))
PORT = int(os.environ.get("PORT", "10000"))

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)

# --- Plaza ---
PLAZA_LIST_URL = (
    "https://plaza.newnewnew.space/en/availables-places/living-place"
    "#?gesorteerd-op=prijs%2B"
)
PLAZA_COMPLEX_URL = "https://plaza.newnewnew.space/en/our-complexes/utrecht/limapad"
PLAZA_MATCH_TERMS = ["limapad", "campus 030", "campus030", "usp 030"]
PLAZA_NOTHING_PHRASES = [
    "nothing available",
    "niets beschikbaar",
    "no accommodations",
    "geen aanbod",
    "currently no",
]

# --- Fizz ---
FIZZ_URL = "https://www.the-fizz.com/en/student-accommodation/utrecht/"
FIZZ_FULLY_BOOKED_PHRASES = [
    "currently we are fully booked",
    "we are fully booked",
    "momenteel volgeboekt",
]

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("No Telegram credentials set; would have sent:\n" + text, flush=True)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = json.dumps(
        {"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True}
    ).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print("Telegram status:", resp.status, flush=True)
    except Exception:
        print("Telegram send failed:\n" + traceback.format_exc(), flush=True)


# ---------------------------------------------------------------------------
# Plaza check (Playwright ASYNC API, JS-rendered)
# ---------------------------------------------------------------------------
_browser = None
_pw = None


async def get_browser():
    """Reuse a single browser instance across checks instead of relaunching
    it every cycle — relaunch is by far the slowest part."""
    global _browser, _pw
    if _browser is None:
        _pw = await async_playwright().start()
        _browser = await _pw.chromium.launch(args=["--no-sandbox"])
    return _browser


async def get_text(page, url, wait_ms=4000):
    await page.goto(url, timeout=60000, wait_until="domcontentloaded")
    for sel in [
        "text=/accept/i",
        "text=/accepteren/i",
        "text=/agree/i",
        "button:has-text('OK')",
    ]:
        try:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.click(timeout=1500)
                break
        except Exception:
            pass
    await page.wait_for_timeout(wait_ms)
    chunks = []
    try:
        chunks.append(await page.evaluate("document.body ? document.body.innerText : ''"))
    except Exception:
        pass
    for f in page.frames:
        try:
            t = await f.evaluate("document.body ? document.body.innerText : ''")
            if t:
                chunks.append(t)
        except Exception:
            pass
    return "\n".join(c for c in chunks if c)


def find_plaza_listings(list_text):
    hits = []
    for line in list_text.split("\n"):
        low = line.lower()
        if any(term in low for term in PLAZA_MATCH_TERMS):
            s = line.strip()
            if s:
                hits.append(s)
    return sorted(set(hits))


def plaza_complex_available(complex_text):
    low = complex_text.lower()
    if any(p in low for p in PLAZA_NOTHING_PHRASES):
        return False
    return True


async def check_plaza():
    browser = await get_browser()
    page = await browser.new_page(user_agent=UA, locale="en-GB")
    try:
        list_text = await get_text(page, PLAZA_LIST_URL)
        complex_text = await get_text(page, PLAZA_COMPLEX_URL)
    finally:
        await page.close()

    if "Host not in allowlist" in list_text or "Host not in allowlist" in complex_text:
        return None  # egress-blocked environment, skip

    if len(list_text) < 400 and len(complex_text) < 400:
        return None  # didn't render, skip this cycle

    return {
        "limapad_in_list": find_plaza_listings(list_text),
        "complex_shows_available": plaza_complex_available(complex_text),
    }


def handle_plaza_result(current, prev):
    if current is None:
        return prev  # keep previous state, nothing to compare
    if prev is None:
        print("[Plaza] First run — baseline saved, no alert.", flush=True)
        return current

    newly_listed = [
        x for x in current["limapad_in_list"] if x not in prev.get("limapad_in_list", [])
    ]
    became_available = (
        current["complex_shows_available"] is True
        and prev.get("complex_shows_available") is False
    )

    if newly_listed or became_available:
        parts = ["🏠 Plaza — Limapad / Campus 030 — possible availability!"]
        if newly_listed:
            parts.append("New matching listing(s):\n" + "\n".join(newly_listed))
        if became_available:
            parts.append("The complex page no longer shows 'nothing available'.")
        parts.append("Check now: " + PLAZA_LIST_URL)
        parts.append("Complex page: " + PLAZA_COMPLEX_URL)
        send_telegram("\n\n".join(parts))
        print("[Plaza] CHANGE DETECTED — alert sent.", flush=True)
    else:
        print("[Plaza] No change.", flush=True)

    return current


# ---------------------------------------------------------------------------
# Fizz check (plain HTTP, static HTML) — run in a thread so it never blocks
# the event loop that Plaza's browser checks are running on.
# ---------------------------------------------------------------------------
def _fetch_fizz():
    resp = requests.get(FIZZ_URL, headers={"User-Agent": UA}, timeout=20)
    resp.raise_for_status()
    low = resp.text.lower()
    fully_booked = any(p in low for p in FIZZ_FULLY_BOOKED_PHRASES)
    return {"fully_booked": fully_booked}


async def check_fizz():
    return await asyncio.to_thread(_fetch_fizz)


def handle_fizz_result(current, prev):
    if prev is None:
        print("[Fizz] First run — baseline saved, no alert.", flush=True)
        return current

    became_available = current["fully_booked"] is False and prev.get("fully_booked") is True

    if became_available:
        text = (
            "🏠 THE FIZZ Utrecht — no longer shows 'fully booked'!\n\n"
            "Check now: " + FIZZ_URL
        )
        send_telegram(text)
        print("[Fizz] CHANGE DETECTED — alert sent.", flush=True)
    else:
        print("[Fizz] No change.", flush=True)

    return current


# ---------------------------------------------------------------------------
# Main loop (asyncio)
# ---------------------------------------------------------------------------
async def monitor_loop():
    plaza_state = None
    fizz_state = None

    send_telegram(
        "✅ Monitor started.\nChecking Plaza + THE FIZZ every "
        f"{CHECK_INTERVAL_SECONDS}s."
    )

    while True:
        cycle_start = time.time()

        try:
            plaza_current = await check_plaza()
            plaza_state = handle_plaza_result(plaza_current, plaza_state)
        except Exception:
            print("[Plaza] ERROR:\n" + traceback.format_exc(), flush=True)

        try:
            fizz_current = await check_fizz()
            fizz_state = handle_fizz_result(fizz_current, fizz_state)
        except Exception:
            print("[Fizz] ERROR:\n" + traceback.format_exc(), flush=True)

        elapsed = time.time() - cycle_start
        sleep_for = max(0.0, CHECK_INTERVAL_SECONDS - elapsed)
        print(f"Cycle took {elapsed:.1f}s, sleeping {sleep_for:.1f}s", flush=True)
        await asyncio.sleep(sleep_for)


# ---------------------------------------------------------------------------
# Tiny keep-alive HTTP server (Render requires something bound to $PORT).
# Runs in a plain background thread — no asyncio involved, so it can't
# interfere with the event loop the monitor loop owns.
# ---------------------------------------------------------------------------
class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Monitor is running.")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()

    def log_message(self, format, *args):
        pass  # silence default request logging


def run_ping_server():
    server = HTTPServer(("0.0.0.0", PORT), PingHandler)
    server.serve_forever()


if __name__ == "__main__":
    threading.Thread(target=run_ping_server, daemon=True).start()
    asyncio.run(monitor_loop())    asyncio.run(monitor_loop())
