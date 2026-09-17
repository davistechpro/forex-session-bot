from dotenv import load_dotenv
import os, requests

load_dotenv()
token = os.getenv("TELEGRAM_BOT_TOKEN")
r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates")
data = r.json()

seen = {}
for u in data.get("result", []):
    msg = u.get("message") or u.get("my_chat_member") or {}
    chat = msg.get("chat")
    if chat:
        seen[chat["id"]] = chat.get("title") or chat.get("username") or "(private)"

if seen:
    for cid, title in seen.items():
        kind = "GROUP" if cid < 0 else "private"
        print(f"{kind:8} {cid}   {title}")
else:
    print("No chats found. Send a message in the group, then rerun.")
