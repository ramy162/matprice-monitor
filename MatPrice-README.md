# MatPrice Monitor — Telethon Edition
## Reads ANY Telegram channel you're a member of. No bot needed.

---

## What you need (all free)

| Thing | Where to get it | Time |
|---|---|---|
| Python 3.8+ | https://python.org/downloads | 5 min |
| Telegram API credentials | https://my.telegram.org | 2 min |
| Google Gemini API key | https://aistudio.google.com | 2 min |

---

## Step 1 — Install Python
Download from https://python.org/downloads
During install, check ✅ **"Add Python to PATH"**

---

## Step 2 — Get Telegram API credentials (free)
1. Go to **https://my.telegram.org**
2. Log in with your phone number
3. Click **"API development tools"**
4. Fill in any app name (e.g. "MatPrice") and platform (Desktop)
5. Copy your **api_id** (a number) and **api_hash** (a long string)

> ⚠️ These are YOUR personal credentials — keep them private.
> This is what lets the script read channels as YOU (not a bot).

---

## Step 3 — Get free Gemini API key
1. Go to **https://aistudio.google.com**
2. Sign in with any Google account
3. Click **"Get API Key"** → **"Create API key"**
4. Copy it (starts with `AIza...`)

---

## Step 4 — Install the script
1. Download the `matprice-monitor` folder
2. Open a terminal / command prompt in that folder
3. Run:
```
pip install -r requirements.txt
```

---

## Step 5 — Run for the first time
```
python monitor.py
```

It will ask you for:
- Your Telegram API ID and Hash
- Your Gemini API key
- Your phone number (to log in to Telegram)
- Which channels to monitor (type @username or the name)
- Alert threshold % and schedule interval

After that, a `config.json` file is saved. You only do this once.

Telegram will send a login code to your phone — enter it when asked.

---

## Everyday usage

| Command | What it does |
|---|---|
| `python monitor.py` | Start scheduled monitoring |
| `python monitor.py run` | Run once immediately |
| `python monitor.py add-channel` | Add a new channel |
| `python monitor.py list-channels` | See all monitored channels |
| `python monitor.py remove-channel` | Remove a channel |

---

## Output
Every run creates a PNG image in the `output/` folder named:
```
output/matprice_20250601_0800.png
```

The image shows:
- All prices grouped by category (Cement, Steel, Sand, Timber, etc.)
- % change vs last run (green = down, red = up)
- 2-sentence market summary
- Which channel each price came from

---

## Keep it running 24/7 (optional)
**Windows:** Create a Task Scheduler task to run `python monitor.py` at startup
**Mac/Linux:** Add to crontab:
```
@reboot cd /path/to/matprice && python monitor.py
```

---

## Troubleshooting

**"No messages collected"**
→ Make sure you are actually a member of the channel in Telegram
→ Use the exact @username (e.g. `@constructionprices`)

**"FloodWaitError"**
→ Telegram rate-limited the account. Wait the number of seconds shown, then retry.

**"SessionPasswordNeededError"**
→ Your Telegram account has 2-step verification. The script will ask for your password.

**Gemini returns no prices**
→ The channel messages may not contain clear price text. Try channels that post prices in text format (not just images).
