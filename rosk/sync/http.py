"""Tiny JSON-over-HTTPS helper on top of urllib (runs in the sync worker thread)."""

import json
import urllib.error
import urllib.parse
import urllib.request


class HttpError(Exception):
    def __init__(self, status: int, message: str, body: str = ""):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.body = body

    @property
    def retryable(self) -> bool:
        return self.status in (0, 408, 429) or self.status >= 500


class AuthError(HttpError):
    pass


def request(method: str, url: str, token: str, params=None, body=None, timeout=30):
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        url += "?" + urllib.parse.urlencode(clean)
    data = None
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        text = e.read().decode(errors="replace")
        message = text
        try:
            message = json.loads(text)["error"]["message"]
        except Exception:
            pass
        cls = AuthError if e.code == 401 else HttpError
        raise cls(e.code, message, text) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise HttpError(0, f"network error: {e}") from None
    if not raw:
        return None
    return json.loads(raw)
