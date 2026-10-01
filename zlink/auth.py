"""User accounts storage, with salted PBKDF2-SHA256 password hashes."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

USERNAME_RE = re.compile(r'^[A-Za-z0-9_.-]{2,24}$')
MIN_PASSWORD_LENGTH = 4
ITERATIONS = 200_000


class AuthError(Exception):
    pass


def validate_username(username: str) -> str:
    if not isinstance(username, str) or not USERNAME_RE.match(username):
        raise AuthError('username must be 2-24 characters among letters, digits, "_", "-" and "."')
    return username


def _hash(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac('sha256', password.encode(), salt, iterations)


class UserStore:
    """Registered accounts persisted as a JSON file. Safe to use from several threads."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._users: dict[str, dict] = json.loads(self.path.read_text()) if self.path.exists() else {}

    def __contains__(self, username: str) -> bool:
        return username in self._users

    def __len__(self) -> int:
        return len(self._users)

    def register(self, username: str, password: str) -> None:
        validate_username(username)
        if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
            raise AuthError(f'password must be at least {MIN_PASSWORD_LENGTH} characters long')
        salt = os.urandom(16)
        with self._lock:
            if username in self._users:
                raise AuthError(f'username "{username}" is already taken')
            self._users[username] = {
                'salt': salt.hex(),
                'hash': _hash(password, salt, ITERATIONS).hex(),
                'iterations': ITERATIONS,
                'created': datetime.now().isoformat(timespec='seconds'),
            }
            self._save()

    def verify(self, username: str, password: str) -> bool:
        record = self._users.get(username)
        if record is None or not isinstance(password, str):
            return False
        digest = _hash(password, bytes.fromhex(record['salt']), record['iterations'])
        return hmac.compare_digest(digest.hex(), record['hash'])

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self._users, indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
