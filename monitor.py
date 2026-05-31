"""
MatPrice Monitor — Telethon Edition
Reads any Telegram channel you're a member of (no bot needed).
Uses Groq AI (free) to analyze prices and generates a summary image.
"""

import asyncio
import json
import os
import sys
import time
import re
from datetime import datetime, timedelta
from pathlib import Path

# ── Check dependencies ────────────────────────────────────────────────────────
try:
    from telethon import TelegramClient
    from telethon.tl.types import Channel, Chat
except ImportError:
    print("❌  Missing dependency. Run:  pip install telethon")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("❌  Missing dependency. Run:  pip install requests")
    sys.exit(1)

try:
    from PIL import Image, ImageDraw, ImageFont
    import textwrap
except ImportError:
    print("❌  Missing dependency. Run:  pip install Pillow")
    sys.exit(1)

# ── Load config ───────────────────────────────────────────────────────────────
CONFIG_FILE = Path("config.json")

def load_config():
    if not CONFIG_FILE.exists():
        print("\n⚠️  config.json not found. Running first-time setup...\n")
        return setup_wizard()
    with open(CONFIG_FILE) as f:
        return json.load(f)

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    print("✅  Config saved to config.json")

def setup_wizard():
    print("=" * 55)
    print("   MatPrice Monitor — First Time Setup")
    print("=" * 55)
    print()
    print("You need 3 things (all free):")
    print()
    print("1. Telegram API credentials (free)")
    print("   → Go to https://my.telegram.org")
    print("   → Log in → 'API development tools'")
    print("   → Create app → copy api_id and api_hash")
    print()
    print("2. Groq API key (free, no credit card)")
    print("   → Go to https://console.groq.com")
    print("   → Sign up → API Keys → Create API key")
    print("   → Key starts with: gsk_...")
    print()

    api_id    = input("Paste your Telegram API ID:    ").strip()
    api_hash  = input("Paste your Telegram API Hash:  ").strip()
    groq_key  = input("Paste your Groq API key:       ").strip()
    phone     = input("Your Telegram phone number (e.g. +251911...): ").strip()

    print()
    print("Now enter the Telegram channels/groups to monitor.")
    print("You can use @username or just the name as it appears in Telegram.")
    print("Type each one and press Enter. Type 'done' when finished.")
    print()

    channels = []
    while True:
        ch = input(f"  Channel {len(channels)+1} (or 'done'): ").strip()
        if ch.lower() == "done":
            break
        if ch:
            channels.append(ch)

    alert_threshold = input("\nAlert if price changes more than (%) [default 5]: ").strip()
    alert_threshold = float(alert_threshold) if alert_threshold else 5.0

    schedule_hours = input("Auto-run every how many hours? (e.g. 12) [default 12]: ").strip()
    schedule_hours = float(schedule_hours) if schedule_hours else 12.0

    cfg = {
        "api_id": int(api_id),
        "api_hash": api_hash,
        "groq_key": groq_key,
        "phone": phone,
        "channels": channels,
        "alert_threshold_pct": alert_threshold,
        "schedule_hours": schedule_hours,
        "messages_per_channel": 50,
        "output_dir": "output"
    }
    save_config(cfg)
    return cfg


# ── Telegram fetcher ──────────────────────────────────────────────────────────
async def fetch_messages(client, channel_identifier, limit=50):
    """Fetch recent messages from any channel/group you're a member of."""
    try:
        entity = await client.get_entity(channel_identifier)
        messages = await client.get_messages(entity, limit=limit)
        texts = []
        for msg in messages:
            if msg.text and msg.text.strip():
                texts.append(msg.text.strip())
        print(f"  ✅  {channel_identifier}: {len(texts)} messages fetched")
        return texts
    except Exception as e:
        print(f"  ❌  {channel_identifier}: {e}")
        return []


# ── Groq analyzer (free) ─────────────────────────────────────────────────────
def analyze_with_groq(groq_key, all_messages, threshold_pct=5.0):
    """Send messages to Groq (free, llama3) and get structured price data back."""

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

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {groq_key}",
        "Content-Type": "application/json"
    }
    body = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 2000,
        "temperature": 0.1
    }

    resp = requests.post(url, headers=headers, json=body, timeout=30)
    if not resp.ok:
        raise Exception(f"Groq {resp.status_code} error: {resp.text[:500]}")
    data = resp.json()

    if "error" in data:
        raise Exception(f"Groq error: {data['error']['message']}")

    raw = data["choices"][0]["message"]["content"]
    clean = re.sub(r"```json|```", "", raw).strip()
    return json.loads(clean)


# ── Image generator ───────────────────────────────────────────────────────────
CATEGORY_COLORS = {
    "Cement":           "#c8f04a",
    "Steel & Iron":     "#4af0c8",
    "Sand & Aggregate": "#f0a832",
    "Timber & Wood":    "#a06af0",
    "Blocks & Bricks":  "#f04a4a",
    "Other Materials":  "#4a80f0",
}
BG        = (14, 15, 14)
BG2       = (22, 23, 20)
BG3       = (30, 31, 28)
BORDER    = (42, 43, 40)
TEXT      = (232, 230, 222)
MUTED     = (120, 120, 112)
ACCENT    = (200, 240, 74)

def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def draw_summary_image(summary, output_path):
    W = 900
    ROW_H    = 46
    CAT_HEAD = 52
    HEADER   = 170
    FOOTER   = 70

    categories = summary.get("categories", [])
    total_rows = sum(len(c["items"]) for c in categories)
    H = HEADER + len(categories) * CAT_HEAD + total_rows * ROW_H + FOOTER + 20

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # ── Fonts (fallback to default if not available) ──────────────────────────
    def font(size, bold=False):
        for name in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ):
            try:
                return ImageFont.truetype(name, size)
            except:
                pass
        return ImageFont.load_default()

    f_title  = font(30, bold=True)
    f_sub    = font(13)
    f_cat    = font(18, bold=True)
    f_item   = font(14)
    f_price  = font(15, bold=True)
    f_badge  = font(12, bold=True)
    f_muted  = font(11)

    # ── Header ────────────────────────────────────────────────────────────────
    draw.rectangle([0, 0, W, HEADER], fill=BG2)
    draw.rectangle([0, HEADER-2, W, HEADER], fill=ACCENT)  # accent line

    draw.text((40, 28), "MatPrice Monitor", font=f_title, fill=ACCENT)
    draw.text((40, 72), "CONSTRUCTION MATERIAL PRICE DIGEST", font=f_sub, fill=MUTED)
    now_str = datetime.now().strftime("%A, %d %B %Y  •  %H:%M")
    draw.text((40, 96), now_str, font=f_sub, fill=TEXT)

    summary_text = summary.get("summary", "")
    # wrap summary
    chars_per_line = 110
    wrapped = textwrap.fill(summary_text, chars_per_line)
    draw.text((40, 124), wrapped, font=f_muted, fill=(160, 158, 150))

    # ── Categories ────────────────────────────────────────────────────────────
    y = HEADER + 10

    for cat in categories:
        accent_rgb = hex_to_rgb(CATEGORY_COLORS.get(cat["name"], "#c8f04a"))

        # Category header bar
        draw.rectangle([0, y, W, y + CAT_HEAD], fill=BG3)
        draw.rectangle([0, y, 4, y + CAT_HEAD], fill=accent_rgb)
        label = f"{cat.get('icon','•')}  {cat['name']}"
        draw.text((24, y + 14), label, font=f_cat, fill=accent_rgb)
        y += CAT_HEAD

        # Items
        for idx, item in enumerate(cat["items"]):
            row_bg = BG if idx % 2 == 0 else BG2
            draw.rectangle([0, y, W, y + ROW_H], fill=row_bg)

            # Material name
            draw.text((30, y + 14), item.get("name", ""), font=f_item, fill=TEXT)

            # Price + unit
            price_str = item.get("price", "—")
            unit_str  = item.get("unit", "")
            full_price = f"{price_str}  /  {unit_str}" if unit_str else price_str
            draw.text((340, y + 13), full_price, font=f_price, fill=(255, 255, 255))

            # Change badge
            change = item.get("change")
            if change is not None:
                up = change >= 0
                badge_bg  = (58, 26, 26) if up else (26, 46, 26)
                badge_col = (240, 74, 74) if up else (74, 240, 74)
                draw.rounded_rectangle([578, y + 10, 672, y + 36], radius=6, fill=badge_bg)
                arrow = "▲" if up else "▼"
                draw.text((590, y + 13), f"{arrow} {abs(change):.1f}%", font=f_badge, fill=badge_col)

            # Source
            draw.text((700, y + 16), item.get("source", ""), font=f_muted, fill=MUTED)

            y += ROW_H

    # ── Footer ────────────────────────────────────────────────────────────────
    draw.rectangle([0, y, W, y + FOOTER], fill=BG2)
    draw.rectangle([0, y, W, y + 1], fill=BORDER)
    draw.text((40, y + 24), "Generated by MatPrice Monitor  •  Powered by Groq AI (Free)", font=f_muted, fill=MUTED)
    draw.text((W - 130, y + 24), "matprice", font=f_sub, fill=ACCENT)

    img.save(output_path, "PNG")
    print(f"\n✅  Image saved → {output_path}")
    return output_path


# ── Price history & alerts ────────────────────────────────────────────────────
HISTORY_FILE = Path("price_history.json")

def load_history():
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE) as f:
            return json.load(f)
    return {}

def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)

def detect_changes(summary, history, threshold_pct):
    """Compare current prices to last run and annotate % changes."""
    alerts = []
    last = history.get("last_prices", {})

    for cat in summary.get("categories", []):
        for item in cat["items"]:
            key = f"{cat['name']}::{item['name']}"
            # Try to parse price number
            price_num = None
            try:
                price_num = float(re.sub(r"[^\d.]", "", item["price"]))
            except:
                pass

            if price_num and key in last:
                old_num = last[key]
                if old_num > 0:
                    pct = ((price_num - old_num) / old_num) * 100
                    item["change"] = round(pct, 1)
                    if abs(pct) >= threshold_pct:
                        direction = "UP" if pct > 0 else "DOWN"
                        alerts.append(
                            f"⚠️  {item['name']} ({cat['name']}) {direction} {abs(pct):.1f}% "
                            f"— was {old_num:,.0f}, now {price_num:,.0f} ETB"
                        )
            if price_num:
                last[key] = price_num

    history["last_prices"] = last
    history["last_run"] = datetime.now().isoformat()
    return alerts


# ── Main runner ───────────────────────────────────────────────────────────────
async def run_once(cfg):
    print(f"\n{'='*55}")
    print(f"  MatPrice Monitor  —  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*55}")

    Path(cfg.get("output_dir", "output")).mkdir(exist_ok=True)

    # 1. Connect to Telegram as user
    session_file = "matprice_session"
    async with TelegramClient(session_file, cfg["api_id"], cfg["api_hash"]) as client:
        if not await client.is_user_authorized():
            await client.send_code_request(cfg["phone"])
            code = input("\n📱 Enter the code Telegram sent to your phone: ").strip()
            await client.sign_in(cfg["phone"], code)
            print("✅  Logged into Telegram successfully!")

        # 2. Fetch messages from all channels
        print(f"\n📡  Fetching from {len(cfg['channels'])} channel(s)...")
        all_messages = []
        for ch in cfg["channels"]:
            msgs = await fetch_messages(client, ch, limit=cfg.get("messages_per_channel", 50))
            for m in msgs:
                all_messages.append(f"[{ch}] {m}")

    if not all_messages:
        print("\n⚠️  No messages collected. Check your channel names in config.json")
        return

    print(f"\n🤖  Analyzing messages with Groq (free)...")
    summary = analyze_with_groq(cfg["groq_key"], all_messages, cfg.get("alert_threshold_pct", 5))

    if not summary.get("categories"):
        print("⚠️  Groq found no price data in the messages.")
        return

    # 3. Detect price changes vs history
    history = load_history()
    alerts = detect_changes(summary, history, cfg.get("alert_threshold_pct", 5))
    save_history(history)

    if alerts:
        print(f"\n🚨  PRICE ALERTS ({len(alerts)}):")
        for a in alerts:
            print(f"   {a}")
    else:
        print("\n✅  No significant price changes detected.")

    # 4. Generate image
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = Path(cfg.get("output_dir", "output")) / f"matprice_{ts}.png"
    draw_summary_image(summary, out_path)

    # Count items
    total_items = sum(len(c["items"]) for c in summary["categories"])
    print(f"📊  {len(summary['categories'])} categories · {total_items} materials found")
    print(f"\n💬  Market summary: {summary.get('summary','')}")


async def main():
    cfg = load_config()

    # Add channel management CLI
    if len(sys.argv) > 1:
        if sys.argv[1] == "add-channel":
            ch = input("Channel username or name to add: ").strip()
            if ch not in cfg["channels"]:
                cfg["channels"].append(ch)
                save_config(cfg)
                print(f"✅  Added: {ch}")
            else:
                print("Already in list.")
            return
        elif sys.argv[1] == "list-channels":
            print("\nMonitored channels:")
            for i, c in enumerate(cfg["channels"], 1):
                print(f"  {i}. {c}")
            return
        elif sys.argv[1] == "remove-channel":
            print("Current channels:")
            for i, c in enumerate(cfg["channels"], 1):
                print(f"  {i}. {c}")
            idx = int(input("Number to remove: ")) - 1
            removed = cfg["channels"].pop(idx)
            save_config(cfg)
            print(f"✅  Removed: {removed}")
            return
        elif sys.argv[1] == "run":
            await run_once(cfg)
            return

    # Default: scheduled loop
    print(f"\n🕐  Scheduler active — running every {cfg['schedule_hours']} hours")
    print(f"   Channels: {', '.join(cfg['channels'])}")
    print(f"   Alert threshold: {cfg['alert_threshold_pct']}%")
    print(f"   Output folder: {cfg.get('output_dir','output')}/")
    print("\n   Press Ctrl+C to stop.\n")

    while True:
        try:
            await run_once(cfg)
        except Exception as e:
            print(f"\n❌  Error during run: {e}")

        next_run = datetime.now() + timedelta(hours=cfg["schedule_hours"])
        print(f"\n⏳  Next run at {next_run.strftime('%H:%M')}  (Ctrl+C to stop)")
        await asyncio.sleep(cfg["schedule_hours"] * 3600)


if __name__ == "__main__":
    asyncio.run(main())
