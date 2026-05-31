"""
MatPrice Monitor — Railway Edition
Runs 24/7 on Railway free tier.
Reads Telegram channels, analyzes prices with Groq, sends image to your Telegram.
All config via environment variables (no config.json needed on server).
"""

import asyncio
import json
import os
import sys
import re
import io
from datetime import datetime, timedelta
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession
import requests
from PIL import Image, ImageDraw, ImageFont
import textwrap

# ── Config from environment variables ────────────────────────────────────────
def get_cfg():
    missing = []
    cfg = {}
    for key in ["TG_API_ID", "TG_API_HASH", "TG_SESSION", "TG_CHANNELS",
                "GROQ_KEY", "TG_NOTIFY_CHAT"]:
        val = os.environ.get(key, "")
        if not val:
            missing.append(key)
        cfg[key] = val

    if missing:
        print(f"❌  Missing environment variables: {', '.join(missing)}")
        print("\nSet these in Railway → your project → Variables tab.")
        sys.exit(1)

    return {
        "api_id":        int(cfg["TG_API_ID"]),
        "api_hash":      cfg["TG_API_HASH"],
        "session":       cfg["TG_SESSION"],
        "channels":      [c.strip() for c in (cfg["TG_CHANNELS"] or "").split(",") if c.strip()],
        "groq_key":      cfg["GROQ_KEY"],
        "notify_chat":   cfg["TG_NOTIFY_CHAT"],
        "tiktok_accounts": [a.strip() for a in os.environ.get("TIKTOK_ACCOUNTS", "").split(",") if a.strip()],
        "ocr_channels": [a.strip() for a in os.environ.get("OCR_CHANNELS", "").split(",") if a.strip()],
        "apify_token": os.environ.get("APIFY_TOKEN", ""),
        "schedule_hours": float(os.environ.get("SCHEDULE_HOURS", "12")),
        "threshold_pct":  float(os.environ.get("ALERT_THRESHOLD_PCT", "5")),
        "messages_limit": int(os.environ.get("MESSAGES_PER_CHANNEL", "50")),
    }


# ── TikTok scraper ───────────────────────────────────────────────────────────
def fetch_tiktok_captions(username):
    """Scrape recent video captions from a TikTok profile page."""
    username = username.lstrip("@")
    url = f"https://www.tiktok.com/@{username}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        html = resp.text
        texts = []
        descs = re.findall(r'"desc":"([^"]{10,300})"', html)
        for d in descs[:20]:
            try:
                cleaned = d.encode().decode('unicode_escape', errors='ignore')
                if any(c.isdigit() for c in cleaned):
                    texts.append(cleaned)
            except:
                pass
        price_patterns = re.findall(r'[\w\s]{2,30}[\s:]+\d[\d,\.]+\s*(?:birr|ETB)?', html, re.IGNORECASE)
        texts.extend(price_patterns[:10])
        if texts:
            print(f"  ✅  TikTok @{username}: {len(texts)} snippets found")
        else:
            print(f"  ⚠️  TikTok @{username}: no price text found")
        return [f"[TikTok @{username}] {t}" for t in texts]
    except Exception as e:
        print(f"  ❌  TikTok @{username}: {e}")
        return []


# ── Image OCR via Groq vision ────────────────────────────────────────────────
def ocr_image_with_groq(groq_key, image_bytes):
    """Extract text from a price poster image using Groq vision model."""
    import base64
    b64 = base64.b64encode(image_bytes).decode()
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
            json={
                "model": "meta-llama/llama-4-scout-17b-16e-instruct",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        {"type": "text", "text": "This is a construction material price comparison table from Ethiopia. Extract every row as: MATERIAL NAME | OLD PRICE | NEW PRICE | CHANGE. Also extract the dates shown. Return only the extracted data as plain text rows, one per line. Be thorough — extract every single material row you can see."}
                    ]
                }],
                "max_tokens": 500
            },
            timeout=30
        )
        if resp.ok:
            return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"    OCR error: {e}")
    return ""


# ── Telegram fetcher ──────────────────────────────────────────────────────────
async def fetch_messages(client, channel_id, limit=50, groq_key="", ocr_enabled=False):
    try:
        entity = await client.get_entity(channel_id)
        messages = await client.get_messages(entity, limit=limit)
        texts = []
        image_count = 0
        for m in messages:
            if m.text and m.text.strip():
                texts.append(m.text.strip())
            # Download and OCR images (price posters)
            if m.photo and groq_key and ocr_enabled and image_count < 10:
                try:
                    img_bytes = await client.download_media(m.photo, bytes)
                    if img_bytes:
                        ocr_text = ocr_image_with_groq(groq_key, img_bytes)
                        if ocr_text and any(c.isdigit() for c in ocr_text):
                            texts.append(f"[IMAGE OCR] {ocr_text}")
                            image_count += 1
                except Exception as e:
                    pass
        print(f"  ✅  {channel_id}: {len(texts)} messages ({image_count} images OCR'd)")
        return texts
    except Exception as e:
        print(f"  ❌  {channel_id}: {e}")
        return []


# ── Groq analyzer ─────────────────────────────────────────────────────────────
def analyze_with_groq(groq_key, all_messages):
    prompt = f"""You are a construction material price analyst in Ethiopia.
Below are raw messages from Telegram channels about construction material prices.
Extract ALL price mentions and return ONLY valid JSON — no markdown, no explanation.

Return this exact structure:
{{
  "categories": [
    {{
      "name": "Category name",
      "icon": "emoji",
      "items": [
        {{
          "name": "Material name",
          "price": "price as string",
          "unit": "unit (e.g. quintal, m2, piece, kg)",
          "change": null,
          "source": "channel name"
        }}
      ]
    }}
  ],
  "summary": "2-sentence overall market summary in English",
  "alerts": []
}}

Use ONLY these category names (omit if no items found):
- Cement
- Steel & Iron
- Sand & Aggregate
- Timber & Wood
- Blocks & Bricks
- Other Materials

MESSAGES:
{chr(10).join(all_messages[:60])}
"""

    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
        json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}],
              "max_tokens": 2000, "temperature": 0.1},
        timeout=30
    )
    if not resp.ok:
        raise Exception(f"Groq {resp.status_code}: {resp.text[:300]}")
    raw = resp.json()["choices"][0]["message"]["content"]
    clean = re.sub(r"```json|```", "", raw).strip()
    return json.loads(clean)


# ── Price history & change detection ─────────────────────────────────────────
HISTORY_FILE = Path("/tmp/price_history.json")

def load_history():
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE) as f:
            return json.load(f)
    return {}

def save_history(h):
    with open(HISTORY_FILE, "w") as f:
        json.dump(h, f, indent=2)

def detect_changes(summary, history, threshold_pct):
    alerts = []
    last = history.get("last_prices", {})
    for cat in (summary.get("categories") or []):
        for item in cat["items"]:
            key = f"{cat['name']}::{item['name']}"
            try:
                price_num = float(re.sub(r"[^\d.]", "", item["price"]))
            except:
                price_num = None
            if price_num and key in last:
                old = last[key]
                if old > 0:
                    pct = ((price_num - old) / old) * 100
                    item["change"] = round(pct, 1)
                    if abs(pct) >= threshold_pct:
                        direction = "UP 🔴" if pct > 0 else "DOWN 🟢"
                        alerts.append(f"{item['name']} ({cat['name']}) {direction} {abs(pct):.1f}%")
            if price_num:
                last[key] = price_num
    history["last_prices"] = last
    history["last_run"] = datetime.now().isoformat()
    return alerts


# ── Image generator ───────────────────────────────────────────────────────────
CATEGORY_COLORS = {
    "Cement":           "#c8f04a",
    "Steel & Iron":     "#4af0c8",
    "Sand & Aggregate": "#f0a832",
    "Timber & Wood":    "#a06af0",
    "Blocks & Bricks":  "#f04a4a",
    "Other Materials":  "#4a80f0",
}
BG = (14,15,14); BG2 = (22,23,20); BG3 = (30,31,28)
TEXT = (232,230,222); MUTED = (120,120,112); ACCENT = (200,240,74)

def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def get_font(size, bold=False):
    candidates = [
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
        f"/usr/share/fonts/truetype/liberation/LiberationSans-{'Bold' if bold else 'Regular'}.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except:
            pass
    return ImageFont.load_default()

def draw_summary_image(summary):
    W = 900; ROW_H = 46; CAT_HEAD = 52; HEADER = 170; FOOTER = 70
    categories = summary.get("categories", [])
    total_rows = sum(len(c["items"]) for c in categories)
    H = HEADER + len(categories)*CAT_HEAD + total_rows*ROW_H + FOOTER + 20

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    f_title = get_font(30, bold=True)
    f_sub   = get_font(13)
    f_cat   = get_font(18, bold=True)
    f_item  = get_font(14)
    f_price = get_font(15, bold=True)
    f_badge = get_font(12, bold=True)
    f_muted = get_font(11)

    # Header
    draw.rectangle([0,0,W,HEADER], fill=BG2)
    draw.rectangle([0,HEADER-2,W,HEADER], fill=ACCENT)
    draw.text((40,28), "MatPrice Monitor", font=f_title, fill=ACCENT)
    draw.text((40,72), "CONSTRUCTION MATERIAL PRICE DIGEST", font=f_sub, fill=MUTED)
    draw.text((40,96), datetime.now().strftime("%A, %d %B %Y  •  %H:%M"), font=f_sub, fill=TEXT)
    wrapped = textwrap.fill(summary.get("summary",""), 110)
    draw.text((40,124), wrapped, font=f_muted, fill=(160,158,150))

    y = HEADER + 10
    for cat in categories:
        accent_rgb = hex_to_rgb(CATEGORY_COLORS.get(cat["name"], "#c8f04a"))
        draw.rectangle([0,y,W,y+CAT_HEAD], fill=BG3)
        draw.rectangle([0,y,4,y+CAT_HEAD], fill=accent_rgb)
        draw.text((24,y+14), f"{cat.get('icon','•')}  {cat['name']}", font=f_cat, fill=accent_rgb)
        y += CAT_HEAD

        for idx, item in enumerate(cat["items"]):
            draw.rectangle([0,y,W,y+ROW_H], fill=BG if idx%2==0 else BG2)
            draw.text((30,y+14), item.get("name",""), font=f_item, fill=TEXT)
            price_str = item.get("price","—")
            unit_str  = item.get("unit","")
            draw.text((340,y+13), f"{price_str}  /  {unit_str}" if unit_str else price_str, font=f_price, fill=(255,255,255))
            change = item.get("change")
            if change is not None:
                up = change >= 0
                draw.rounded_rectangle([578,y+10,672,y+36], radius=6, fill=(58,26,26) if up else (26,46,26))
                draw.text((590,y+13), f"{'▲' if up else '▼'} {abs(change):.1f}%", font=f_badge, fill=(240,74,74) if up else (74,240,74))
            draw.text((700,y+16), item.get("source",""), font=f_muted, fill=MUTED)
            y += ROW_H

    draw.rectangle([0,y,W,y+FOOTER], fill=BG2)
    draw.rectangle([0,y,W,y+1], fill=(42,43,40))
    draw.text((40,y+24), "MatPrice Monitor  •  Powered by Groq AI (Free)", font=f_muted, fill=MUTED)
    draw.text((W-130,y+24), "matprice", font=f_sub, fill=ACCENT)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


# ── Send image + alerts to Telegram ──────────────────────────────────────────
def send_to_telegram(bot_token, chat_id, image_buf, alerts, summary):
    # Send image
    caption = f"📊 MatPrice Digest — {datetime.now().strftime('%d %b %Y %H:%M')}\n\n"
    caption += summary.get("summary", "")
    if alerts:
        caption += f"\n\n🚨 PRICE ALERTS:\n" + "\n".join(f"• {a}" for a in alerts)

    resp = requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendPhoto",
        data={"chat_id": chat_id, "caption": caption},
        files={"photo": ("matprice.png", image_buf, "image/png")},
        timeout=30
    )
    if resp.ok:
        print(f"✅  Sent to Telegram chat {chat_id}")
    else:
        print(f"❌  Telegram send failed: {resp.text[:200]}")


# ── Apify TikTok slide scraper ────────────────────────────────────────────────
def fetch_tiktok_slides_apify(apify_token, tiktok_accounts):
    """Use Apify TikTok Scraper to get slide images from TikTok profiles."""
    if not apify_token or not tiktok_accounts:
        return []

    profile_urls = [f"https://www.tiktok.com/@{a.lstrip('@')}" for a in tiktok_accounts]

    # Start Apify actor run
    try:
        start_resp = requests.post(
            "https://api.apify.com/v2/acts/clockworks~tiktok-scraper/runs",
            headers={"Authorization": f"Bearer {apify_token}", "Content-Type": "application/json"},
            json={
                "profiles": profile_urls,
                "resultsPerPage": 10,
                "shouldDownloadCovers": True,
                "shouldDownloadVideos": False,
                "shouldDownloadSubtitles": False,
            },
            timeout=30
        )
        if not start_resp.ok:
            print(f"  ❌  Apify start failed: {start_resp.text[:200]}")
            return []

        run_id = start_resp.json()["data"]["id"]
        print(f"  ⏳  Apify TikTok scrape started (run {run_id})...")

        # Wait for completion (max 60 seconds)
        import time
        for _ in range(12):
            time.sleep(5)
            status_resp = requests.get(
                f"https://api.apify.com/v2/actor-runs/{run_id}",
                headers={"Authorization": f"Bearer {apify_token}"},
                timeout=15
            )
            status = status_resp.json()["data"]["status"]
            if status == "SUCCEEDED":
                break
            elif status in ("FAILED", "ABORTED", "TIMED-OUT"):
                print(f"  ❌  Apify run {status}")
                return []

        # Get results
        results_resp = requests.get(
            f"https://api.apify.com/v2/actor-runs/{run_id}/dataset/items",
            headers={"Authorization": f"Bearer {apify_token}"},
            timeout=15
        )
        items = results_resp.json()
        image_urls = []
        for item in items:
            source = item.get("authorMeta", {}).get("name", "") or item.get("author", "") or "TikTok"
            # Try all known slide image fields
            slides = (item.get("imagePost") or item.get("photoImages") or
                      item.get("slideshowImages") or item.get("imageList") or [])
            for img in slides:
                url = (img.get("imageURL") or img.get("url") or img.get("imageUrl")
                       or img.get("thumbUrl") or (img if isinstance(img, str) else None))
                if url:
                    image_urls.append((url, source))
            # Fallback: cover/thumbnail
            if not slides:
                for field in ("covers", "coversMedium", "coversLarge"):
                    covers = item.get(field) or []
                    for c in covers[:1]:
                        url = c if isinstance(c, str) else c.get("url", "")
                        if url:
                            image_urls.append((url, source))
                            break

        if items:
            sample = items[0]
            keys = list(sample.keys())
            print(f"  🔍  Apify item keys: {keys[:15]}")
        print(f"  ✅  Apify TikTok: {len(image_urls)} slide images found")
        return image_urls

    except Exception as e:
        print(f"  ❌  Apify error: {e}")
        return []


def ocr_tiktok_slides(groq_key, apify_token, tiktok_accounts):
    """Download TikTok slide images and OCR them."""
    image_urls = fetch_tiktok_slides_apify(apify_token, tiktok_accounts)
    texts = []
    for url, source in image_urls[:10]:
        try:
            img_resp = requests.get(url, timeout=15)
            if img_resp.ok:
                ocr_text = ocr_image_with_groq(groq_key, img_resp.content)
                if ocr_text and any(c.isdigit() for c in ocr_text):
                    texts.append(f"[TikTok @{source}] {ocr_text}")
        except Exception as e:
            pass
    print(f"  ✅  TikTok slides OCR: {len(texts)} images with prices")
    return texts


# ── Main ──────────────────────────────────────────────────────────────────────
async def run_once(cfg):
    print(f"\n{'='*55}")
    print(f"  MatPrice Monitor  —  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*55}")

    async with TelegramClient(StringSession(cfg["session"]), cfg["api_id"], cfg["api_hash"]) as client:
        print(f"\n📡  Fetching from {len(cfg['channels'])} channel(s)...")
        all_messages = []
        for ch in (cfg["channels"] or []):
            ocr_on = ch in (cfg.get("ocr_channels") or [])
            msgs = await fetch_messages(client, ch, cfg["messages_limit"], cfg["groq_key"], ocr_on)
            for m in msgs:
                all_messages.append(f"[{ch}] {m}")

    # Fetch TikTok slide images via Apify and OCR them
    if cfg.get("apify_token") and cfg.get("tiktok_accounts"):
        tt_texts = ocr_tiktok_slides(cfg["groq_key"], cfg["apify_token"], cfg["tiktok_accounts"])
        all_messages.extend(tt_texts)
    elif cfg.get("tiktok_accounts"):
        for tt in cfg["tiktok_accounts"]:
            tt_msgs = fetch_tiktok_captions(tt)
            all_messages.extend(tt_msgs)

    if not all_messages:
        print("⚠️  No messages collected.")
        return

    print(f"\n🤖  Analyzing {len(all_messages)} messages with Groq...")
    summary = analyze_with_groq(cfg["groq_key"], all_messages)

    if not summary.get("categories"):
        print("⚠️  No price data found.")
        return

    history = load_history()
    alerts = detect_changes(summary, history, cfg["threshold_pct"])
    save_history(history)

    if alerts:
        print(f"\n🚨  {len(alerts)} price alert(s):")
        for a in alerts: print(f"   {a}")
    else:
        print("✅  No significant changes.")

    image_buf = draw_summary_image(summary)
    total_items = sum(len(c["items"]) for c in summary["categories"])
    print(f"📊  {len(summary['categories'])} categories · {total_items} materials")

    # Send to Telegram
    bot_token = os.environ.get("TG_BOT_TOKEN", "")
    if bot_token and cfg["notify_chat"]:
        send_to_telegram(bot_token, cfg["notify_chat"], image_buf, alerts, summary)
    else:
        print("⚠️  TG_BOT_TOKEN not set — skipping Telegram send")

    print(f"💬  {summary.get('summary','')}")


async def main():
    cfg = get_cfg()
    print(f"🕐  Scheduler: every {cfg['schedule_hours']}h | {len(cfg['channels'])} channels | threshold {cfg['threshold_pct']}%")

    while True:
        try:
            await run_once(cfg)
        except Exception as e:
            print(f"\n❌  Error: {e}")
        next_run = datetime.now() + timedelta(hours=cfg["schedule_hours"])
        print(f"\n⏳  Next run at {next_run.strftime('%H:%M')}")
        await asyncio.sleep(cfg["schedule_hours"] * 3600)


if __name__ == "__main__":
    asyncio.run(main())
