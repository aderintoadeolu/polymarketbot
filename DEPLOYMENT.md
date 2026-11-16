# 🚀 Free 24/7 Cloud Deployment Guide

Run your Polymarket Bot continuously in the cloud for free with **zero local data consumption**.

---

## Option 1: Hugging Face Spaces (Recommended — Easiest & 100% Free)
* **Cost:** 100% Free
* **Credit Card:** Not required
* **Resources:** 2 vCPU, 16GB RAM, 24/7 continuous uptime
* **Outbound Internet:** Unlimited (connects to Polymarket with no limits)

### Steps:
1. Go to [huggingface.co](https://huggingface.co) and sign up / log in (free).
2. Click **New Space** (or go to `huggingface.co/new-space`).
3. Fill in:
   * **Space name:** `polymarket-bot` (or any name you like)
   * **License:** Apache 2.0 or MIT
   * **Space SDK:** Choose **Docker** -> **Blank**
   * **Visibility:** Public or Private (Private keeps your data visible only to you)
4. Click **Create Space**.
5. Upload the files from `polymarket_bot` (either via the web "Upload files" button or `git push`):
   * `app.py`
   * `config.py`
   * `market_scanner.py`
   * `orderbook.py`
   * `paper_trader.py`
   * `strategies.py`
   * `telemetry.py`
   * `live_trader.py`
   * `requirements.txt`
   * `Dockerfile`
6. Click **Commit changes**.

Hugging Face will automatically build the container and give you a live URL:
`https://<your-username>-polymarket-bot.hf.space`

You can open this URL on your phone or computer to see the live dashboard!

---

## Option 2: Render (Free Web Service)
* **Cost:** 100% Free (750 free instance hours / month)
* **Credit Card:** Not required

### Steps:
1. Push your `polymarket_bot` directory to a GitHub repository.
2. Go to [render.com](https://render.com) and sign in with GitHub.
3. Click **New +** -> **Web Service**.
4. Select your repository.
5. Settings:
   * **Runtime:** Python 3
   * **Build Command:** `pip install -r requirements.txt`
   * **Start Command:** `python app.py`
   * **Instance Type:** Free ($0/mo)
6. Click **Create Web Service**.
7. *(Optional)* To prevent Render's free tier from sleeping after 15 mins of idle web traffic:
   * Go to [cron-job.org](https://cron-job.org) or [uptimerobot.com](https://uptimerobot.com) (both 100% free).
   * Set up a free ping every 10 minutes to your Render URL (`https://your-bot.onrender.com`).
   * This keeps the bot awake and running 24/7!
