import json
import os
from typing import Dict

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "devices.json")

class DeviceRegistry:
    def __init__(self):
        self._tokens: Dict[str, str] = {}
        self._load()

    def _load(self):
        if os.path.exists(DATA_PATH):
            with open(DATA_PATH, "r", encoding="utf-8") as f:
                self._tokens = json.load(f)
        else:
            self._tokens = {}

    def save(self):
        os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
        with open(DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(self._tokens, f, ensure_ascii=False, indent=2)

    def is_valid(self, device_id: str, token: str) -> bool:
        return self._tokens.get(device_id) == token

    def register(self, device_id: str, token: str):
        self._tokens[device_id] = token
        self.save()

registry = DeviceRegistry()
