from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from evernote.edam.error.ttypes import EDAMErrorCode, EDAMSystemException
from evernote.edam.notestore import NoteStore
from evernote.edam.userstore import UserStore
from thrift.protocol import TBinaryProtocol
from thrift.transport import THttpClient

# Python 3.12+ removed key_file/cert_file from HTTPSConnection; thrift 0.21 still
# passes them. Monkey-patch THttpClient.open() to use the modern signature.
import http.client as _http_client


# Default socket timeout (seconds). Without this, Evernote can hang TCP
# connects indefinitely when rate-limiting, making the process look stuck
# instead of throwing a recoverable error.
_DEFAULT_HTTP_TIMEOUT = 60.0


def _patched_open(self):  # noqa: ANN001
    timeout = self._THttpClient__timeout or _DEFAULT_HTTP_TIMEOUT
    if self.scheme == "http":
        self._THttpClient__http = _http_client.HTTPConnection(
            self.host, self.port, timeout=timeout
        )
    elif self.scheme == "https":
        self._THttpClient__http = _http_client.HTTPSConnection(
            self.host, self.port, timeout=timeout, context=self.context,
        )
    if self.using_proxy():
        self._THttpClient__http.set_tunnel(
            self.realhost, self.realport, {"Proxy-Authorization": self.proxy_auth}
        )


THttpClient.THttpClient.open = _patched_open

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / ".cache"


@dataclass
class Config:
    token: str
    sandbox: bool

    @classmethod
    def load(cls) -> "Config":
        load_dotenv(PROJECT_ROOT / ".env")
        token = os.environ.get("EVERNOTE_DEV_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "EVERNOTE_DEV_TOKEN is not set. "
                "Generate one at https://www.evernote.com/api/DeveloperToken.action "
                "and put it in .env"
            )
        sandbox = os.environ.get("EVERNOTE_SANDBOX", "0").strip() in {"1", "true", "yes"}
        return cls(token=token, sandbox=sandbox)

    @property
    def host(self) -> str:
        return "sandbox.evernote.com" if self.sandbox else "www.evernote.com"


def _store(url: str, store_module):
    transport = THttpClient.THttpClient(url)
    protocol = TBinaryProtocol.TBinaryProtocol(transport)
    return store_module.Client(protocol)


class TokenStore:
    """Wraps a Thrift store client and auto-injects the auth token as the first arg."""

    def __init__(self, client, token: str):
        self._client = client
        self._token = token

    def __getattr__(self, name: str):
        attr = getattr(self._client, name)
        if not callable(attr):
            return attr
        token = self._token

        def wrapped(*args, **kwargs):
            return attr(token, *args, **kwargs)
        return wrapped


class EvernoteClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._user_store: TokenStore | None = None
        self._note_store: TokenStore | None = None

    def get_user_store(self) -> TokenStore:
        if self._user_store is None:
            url = f"https://{self.cfg.host}/edam/user"
            self._user_store = TokenStore(_store(url, UserStore), self.cfg.token)
        return self._user_store

    def get_note_store(self) -> TokenStore:
        if self._note_store is None:
            urls = self.get_user_store().getUserUrls()
            note_store_url = urls.noteStoreUrl
            self._note_store = TokenStore(_store(note_store_url, NoteStore), self.cfg.token)
        return self._note_store


def make_client(cfg: Config | None = None) -> EvernoteClient:
    return EvernoteClient(cfg or Config.load())


def call_with_retry(fn, *args, max_attempts: int = 8, _log=None, **kwargs):
    """Run an Evernote API call, retrying on rate-limit and transient network errors.

    If ``_log`` is provided it gets called with ('RATELIMIT'|'NETERROR', message)
    so the operator can see what's happening — silent multi-minute waits make
    the process look stuck.
    """
    import socket
    for attempt in range(1, max_attempts + 1):
        try:
            return fn(*args, **kwargs)
        except EDAMSystemException as e:
            if e.errorCode == EDAMErrorCode.RATE_LIMIT_REACHED and attempt < max_attempts:
                wait = int(getattr(e, "rateLimitDuration", 30)) + 1
                if _log:
                    _log("RATELIMIT", f"sleeping {wait}s (attempt {attempt}/{max_attempts})")
                time.sleep(wait)
                continue
            raise
        except (socket.timeout, ConnectionError, OSError) as e:
            if attempt < max_attempts:
                wait = min(60, 2 ** attempt)  # 2,4,8,16,32,60,60,60
                if _log:
                    _log("NETERROR", f"{type(e).__name__}: {e}; backoff {wait}s (attempt {attempt}/{max_attempts})")
                time.sleep(wait)
                continue
            raise
