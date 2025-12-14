import base64
import json
from typing import Any, Dict

import bcrypt
from cryptography.fernet import Fernet


def _derive_key(password: str, salt: bytes) -> bytes:
    password_bytes = password.encode()
    key = bcrypt.kdf(password=password_bytes, salt=salt, desired_key_bytes=32, rounds=100)
    return base64.urlsafe_b64encode(key)


def encrypt_payload(data: Dict[str, Any], password: str) -> str:
    salt = bcrypt.gensalt()
    key = _derive_key(password, salt)
    cipher = Fernet(key)
    token = cipher.encrypt(json.dumps(data).encode())
    packed = {
        "salt": base64.b64encode(salt).decode(),
        "token": token.decode(),
    }
    return base64.b64encode(json.dumps(packed).encode()).decode()


def decrypt_payload(payload: str, password: str) -> Dict[str, Any]:
    packed = json.loads(base64.b64decode(payload.encode()))
    salt = base64.b64decode(packed["salt"])
    key = _derive_key(password, salt)
    cipher = Fernet(key)
    decoded = cipher.decrypt(packed["token"].encode()).decode()
    return json.loads(decoded)
