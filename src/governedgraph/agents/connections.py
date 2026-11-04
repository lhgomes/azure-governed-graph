"""Process-local, thread-safe SDK transports; never cache member credentials or data."""
from functools import lru_cache
from contextlib import contextmanager
from contextvars import ContextVar
from threading import RLock

_isolated = ContextVar("voice_cold_clients", default=None)


def isolated() -> bool:
    return _isolated.get() is not None


def scoped(key, factory):
    scope = _isolated.get()
    if scope is None:
        return factory()
    cache, lock = scope
    with lock:
        if key not in cache:
            cache[key] = factory()
        return cache[key]


@contextmanager
def cold_clients():
    cache = {}
    marker = _isolated.set((cache, RLock()))
    try:
        yield
    finally:
        _isolated.reset(marker)
        for resource in reversed(list(cache.values())):
            if hasattr(resource, "close"):
                resource.close()


def http_client():
    return scoped("http", _new_http) if isolated() else _shared_http()


@lru_cache(maxsize=1)
def _shared_http():
    return _new_http()


def _new_http():
    import httpx

    return httpx.Client(follow_redirects=False)


def credential():
    return scoped("credential", _new_credential) if isolated() else _shared_credential()


@lru_cache(maxsize=1)
def _shared_credential():
    return _new_credential()


def _new_credential():
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()
