"""Generated files waiting to be downloaded (a WPS draft): kept in memory under a secret token for a short time, then forgotten."""
import secrets
import threading
import time

TTL_SECONDS = 30 * 60
MAX_KEPT = 50


class DraftStore:
    def __init__(self, clock=time.time):
        self.clock = clock
        self._items = {}                    # token -> (made, filename, bytes)
        self._lock = threading.Lock()

    def save(self, data, filename):
        """-> a secret token for the file. Anyone signed in who holds the token can fetch it until it expires."""
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._forget_old()
            while len(self._items) >= MAX_KEPT:
                self._items.pop(min(self._items, key=lambda t: self._items[t][0]))
            self._items[token] = (self.clock(), filename, data)
        return token

    def get(self, token):
        """-> (filename, bytes) or None when the token is unknown or has expired."""
        with self._lock:
            self._forget_old()
            item = self._items.get(token)
        return (item[1], item[2]) if item else None

    def _forget_old(self):
        now = self.clock()
        for token in [t for t, (made, _n, _d) in self._items.items() if now - made > TTL_SECONDS]:
            del self._items[token]
