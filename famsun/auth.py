"""Password hashing, opaque cookie sessions and bounded authentication throttling."""

from __future__ import annotations

import hashlib
import re
import threading
import time
from collections import OrderedDict, deque

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

USERNAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,63}", re.ASCII)
COOKIE = "famsun_session"
TTL = 60 * 60 * 24 * 7
_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
# A structurally valid Argon2id hash incurs the same cost for unknown users.
_DUMMY = "$argon2id$v=19$m=65536,t=3,p=4$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def password_hash(password: str) -> str:
    return _HASHER.hash(password)


def check_password(password, stored) -> bool:
    try:
        return _HASHER.verify(stored or _DUMMY, password) and bool(stored)
    except VerifyMismatchError:
        return False


def token_digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class RateLimit:
    """Process-local sliding window. The deployment enforces a single API worker."""

    def __init__(self, limit: int, seconds: int):
        self.limit = limit
        self.seconds = seconds
        self.entries = OrderedDict()
        self.lock = threading.Lock()

    def accept(self, key: str) -> bool:
        stamp = time.monotonic()
        with self.lock:
            history = self.entries.pop(key, deque())
            while history and stamp - history[0] > self.seconds:
                history.popleft()
            self.entries[key] = history
            if len(self.entries) > 4096:
                self.entries.popitem(last=False)
            if len(history) >= self.limit:
                return False
            history.append(stamp)
            return True
