import json
from pathlib import Path
from typing import Any, Dict, Optional

from .security import encrypt_payload, decrypt_payload

CONFIG_DIR = Path("configs")
CONFIG_DIR.mkdir(exist_ok=True)


class EncryptedConfigStore:
    def __init__(self, name: str):
        self.name = name
        self.file_path = CONFIG_DIR / f"{name}.enc"

    def save(self, data: Dict[str, Any], password: str) -> None:
        encoded = encrypt_payload(data, password)
        self.file_path.write_text(encoded, encoding="utf-8")

    def load(self, password: str) -> Optional[Dict[str, Any]]:
        if not self.file_path.exists():
            return None
        payload = self.file_path.read_text(encoding="utf-8")
        return decrypt_payload(payload, password)

    def exists(self) -> bool:
        return self.file_path.exists()
