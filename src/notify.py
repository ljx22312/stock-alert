"""WxPusher 推送，复用 ~/wechat-notify/config.json。"""
from __future__ import annotations

import json

import requests

API_URL = "https://wxpusher.zjiecode.com/api/send/message"


class Notifier:
    def __init__(self, wxpusher_config_path: str, recipient: str):
        with open(wxpusher_config_path, encoding="utf-8") as f:
            config = json.load(f)
        self.app_token = config["app_token"]
        recipients = config["recipients"]
        if recipient == "all":
            self.uids = [r["uid"] for r in recipients.values()]
        else:
            self.uids = [recipients[recipient]["uid"]]

    def send(self, content: str, summary: str | None = None) -> bool:
        payload = {
            "appToken": self.app_token,
            "content": content,
            "summary": summary or content[:99],
            "contentType": 1,
            "uids": self.uids,
        }
        try:
            resp = requests.post(API_URL, json=payload, timeout=10)
            ok = resp.json().get("code") == 1000
        except Exception:
            ok = False
        return ok
