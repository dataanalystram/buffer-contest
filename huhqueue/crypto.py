"""Encrypt OAuth tokens at rest with Fernet (AES-128-CBC + HMAC)."""
from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class TokenCipher:
    def __init__(self, key: str | None, data_dir: Path):
        if not key:
            key_file = data_dir / "secret.key"
            if key_file.exists():
                key = key_file.read_text().strip()
            else:
                key = Fernet.generate_key().decode()
                key_file.write_text(key)
                try:
                    os.chmod(key_file, 0o600)
                except OSError:  # pragma: no cover - windows
                    pass
        self.key = key.encode() if isinstance(key, str) else key
        self._f = Fernet(self.key)

    def encrypt(self, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        return self._f.encrypt(value.encode()).decode()

    def decrypt(self, value: str | None) -> str | None:
        if not value:
            return None
        try:
            return self._f.decrypt(value.encode()).decode()
        except InvalidToken:
            return None
