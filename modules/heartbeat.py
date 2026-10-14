"""
Heartbeat -- tells an outside monitor (healthchecks.io) "I'm alive".
If the pings stop (power cut, PC asleep, internet down, script crashed),
healthchecks.io notices on its own and alerts the Telegram group.
The URL comes from .env; if it's not set, this does nothing.
"""
import os
import time

import requests

_last = {}


def ping(env_name: str, min_interval: int = 60):
    url = os.getenv(env_name)
    if not url:
        return
    now = time.time()
    if now - _last.get(env_name, 0) < min_interval:
        return
    _last[env_name] = now
    try:
        requests.get(url, timeout=10)
    except Exception as e:
        print(f"heartbeat: ping failed ({e})")
