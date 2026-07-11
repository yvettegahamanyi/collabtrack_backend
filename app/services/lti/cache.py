import threading
import time
import typing as t

from pylti1p3.launch_data_storage.cache import CacheDataStorage


class TTLCache:
    """Simple in-memory TTL cache for LTI launch state (single-instance deployments)."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[object, float | None]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> t.Any:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if expires_at is not None and time.time() > expires_at:
                del self._data[key]
                return None
            return value

    def set(self, key: str, value: object, exp: t.Optional[int] = None) -> None:
        expires_at = time.time() + exp if exp else None
        with self._lock:
            self._data[key] = (value, expires_at)


lti_launch_cache = TTLCache()


class CollabTrackCacheDataStorage(CacheDataStorage):
    def __init__(self) -> None:
        self._cache = lti_launch_cache
        super().__init__()
