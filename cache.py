"""Tiny thread-safe TTL + LRU cache (no external dependencies)."""
import threading
import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, max_items: int = 512, ttl: float = 3600.0):
        self.max_items, self.ttl = max_items, ttl
        self._d: OrderedDict = OrderedDict()
        self._lock = threading.Lock()
        self.hits = self.misses = 0

    def get(self, key):
        with self._lock:
            item = self._d.get(key)
            if item and item[0] > time.time():
                self._d.move_to_end(key)
                self.hits += 1
                return item[1]
            if item:
                del self._d[key]
            self.misses += 1
            return None

    def set(self, key, value, ttl: float | None = None):
        with self._lock:
            self._d[key] = (time.time() + (ttl if ttl is not None else self.ttl), value)
            self._d.move_to_end(key)
            while len(self._d) > self.max_items:
                self._d.popitem(last=False)

    def clear(self):
        with self._lock:
            self._d.clear()

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {"items": len(self._d), "hits": self.hits, "misses": self.misses,
                "hit_rate": round(self.hits / total, 3) if total else None}
