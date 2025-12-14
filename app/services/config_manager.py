import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import bcrypt
from cryptography.fernet import Fernet


CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _derive_key(password: str, salt: bytes) -> bytes:
    """Derive a Fernet-compatible key using bcrypt KDF."""
    key = bcrypt.kdf(
        password=password.encode("utf-8"),
        salt=salt,
        desired_key_bytes=32,
        rounds=12,
    )
    return base64.urlsafe_b64encode(key)


@dataclass
class EncryptedConfig:
    path: Path

    def load(self, password: str) -> Optional[Dict[str, Any]]:
        if not self.path.exists():
            return None

        with open(self.path, "r", encoding="utf-8") as fh:
            stored = json.load(fh)

        salt = base64.b64decode(stored["salt"])
        payload = stored["payload"].encode("utf-8")
        fernet = Fernet(_derive_key(password, salt))
        decrypted = fernet.decrypt(payload)
        return json.loads(decrypted)

    def save(self, password: str, data: Dict[str, Any]) -> None:
        salt = os.urandom(16)
        fernet = Fernet(_derive_key(password, salt))
        token = fernet.encrypt(json.dumps(data).encode("utf-8"))
        serialized = {
            "salt": base64.b64encode(salt).decode("utf-8"),
            "payload": token.decode("utf-8"),
        }
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(serialized, fh, ensure_ascii=False, indent=2)


PRICE_CONFIG = EncryptedConfig(CONFIG_DIR / "price_config.enc")
AUTOMATION_CONFIG = EncryptedConfig(CONFIG_DIR / "automation_config.enc")
