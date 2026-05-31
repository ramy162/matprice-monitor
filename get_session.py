import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
import json, pathlib

cfg = json.loads(pathlib.Path("config.json").read_text(encoding="utf-8"))

async def main():
    # Load from existing file session and convert to string
    client = TelegramClient("matprice_session", cfg["api_id"], cfg["api_hash"])
    await client.connect()
    
    if not await client.is_user_authorized():
        print("Not logged in! Run monitor.py first.")
        await client.disconnect()
        return

    # Export as string session
    string_session = StringSession.save(client.session)
    
    await client.disconnect()
    
    print("\n" + "="*60)
    print("YOUR SESSION STRING:")
    print("="*60)
    print(string_session)
    print("="*60)
    print("\nCopy the string above and paste it as TG_SESSION in Railway.\n")

asyncio.run(main())
