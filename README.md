# Plaza + THE FIZZ availability monitor (Telegram)

Watches two Utrecht student-housing pages and pings you on Telegram the
moment either shows signs of new availability:

- **Plaza — Limapad / Campus 030** (JS-rendered, checked with Playwright)
- **THE FIZZ — Utrecht** (plain HTML, checked with a fast HTTP request)

Runs as one always-on process, checking both roughly every
`CHECK_INTERVAL_SECONDS` (default 20s). No GitHub Actions — that platform
can't run more often than ~5 minutes.

## 1. Push this folder to a GitHub repo

```
git init
git add .
git commit -m "monitor"
git remote add origin <your-repo-url>
git push -u origin main
```

## 2. Deploy on Render (free, no card required)

1. Go to https://render.com and sign up (GitHub login is easiest).
2. **New +** → **Web Service** → connect your repo.
3. Render should auto-detect `render.yaml`. If not, set manually:
   - **Build Command:** `bash build.sh`
   - **Start Command:** `python monitor.py`
   - **Plan:** Free
4. Add environment variables (Render dashboard → Environment):
   - `TELEGRAM_TOKEN` — from @BotFather
   - `TELEGRAM_CHAT_ID` — your chat/user id (message @userinfobot to get it)
   - `CHECK_INTERVAL_SECONDS` — optional, default 20
5. Deploy. Watch the logs — you should see "Monitor started" and then a
   Telegram message confirming it's alive.

## 3. Keep it awake (free tier sleeps after 15 min idle)

1. Copy your Render service's public URL (e.g. `https://plaza-fizz-monitor.onrender.com`).
2. Go to https://uptimerobot.com, sign up free (no card).
3. **Add New Monitor** → HTTP(s) → paste your Render URL → interval **5 minutes**.

That keeps Render pinged so it never spins down, and the monitor loop inside
keeps checking both sites every ~20s regardless.

## Notes / limits

- Render's free tier has 512MB RAM. Playwright + Chromium is the heavy part
  (for Plaza only — Fizz doesn't need a browser at all). If you see
  out-of-memory crashes in the logs, raise `CHECK_INTERVAL_SECONDS` a bit,
  or ask me to switch Plaza's check to a lighter method if we can find its
  underlying data endpoint.
- State is kept in memory. If Render restarts your instance (it can happen
  occasionally on the free tier), you'll get one silent baseline check with
  no false alert, same as before.
- Please don't drop `CHECK_INTERVAL_SECONDS` far below ~15-20s — much
  faster risks IP blocks on Plaza's side (it needs a real browser render
  each time) and is inconsiderate to a small student-housing operator's
  server.
