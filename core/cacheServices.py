import time

class CacheServices:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super().__new__(cls)
            cls._instance._cache = {}
        return cls._instance

    def set(self, key, value, ttl_seconds):
        expire_at = time.time() + ttl_seconds
        self._cache[key] = (value, expire_at)

    def get(self, key):
        item = self._cache.get(key)
        if not item:
            return None
        value, expire_at = item
        if time.time() > expire_at:
            del self._cache[key]
            return None
        return value

    @staticmethod
    def instance():
        return CacheServices()