"""上游转发：URL 拼装、header 白名单、SSE 透传、凭证脱敏。"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator
from urllib.parse import urlparse, urlunparse

import httpx

from . import config

# 请求方向白名单（客户端 → 上游）
_REQ_HEADER_ALLOW = {
    "content-type",
    "accept",
    "openai-beta",
    "idempotency-key",
    "x-client-request-id",
    "anthropic-beta",
    "user-agent",
}

# 响应方向白名单（上游 → 客户端）
_RESP_HEADER_ALLOW = {
    "content-type",
    "x-request-id",
    "request-id",
    "retry-after",
    "openai-processing-ms",
}
_RESP_HEADER_PREFIX = ("anthropic-ratelimit-", "x-ratelimit-")

_ROUTE_MAP = {
    "chat/completions": "chat/completions",
    "messages": "messages",
    "models": "models",
    "responses": "responses",
}


class UpstreamConfigError(ValueError):
    pass


def upstream_url(endpoint: str, route: str) -> str:
    """拼装上游 URL。endpoint 可带或不带 /v1，自动补全。"""
    endpoint = (endpoint or "").strip()
    if not endpoint:
        raise UpstreamConfigError("upstream_base 未配置")
    u = urlparse(endpoint)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise UpstreamConfigError("upstream_base 必须是 http(s) URL")
    if u.username or u.password or u.query or u.fragment:
        raise UpstreamConfigError("upstream_base 不允许包含认证信息、查询串或片段")
    base = u.path.rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    clean = route.lstrip("/")
    if clean.startswith("v1/"):
        clean = clean[3:]
    return urlunparse((u.scheme, u.netloc, f"{base}/{clean}", "", "", ""))


def build_request_headers(incoming: dict[str, str], protocol: str) -> dict[str, str]:
    """构造上游请求头。key 原样透传，不校验、不存储。"""
    out: dict[str, str] = {}
    lower = {k.lower(): v for k, v in incoming.items()}
    for k in _REQ_HEADER_ALLOW:
        if k in lower and lower[k]:
            out[k] = lower[k]

    if protocol == "anthropic":
        key = lower.get("x-api-key") or _bearer(lower.get("authorization"))
        if key:
            out["x-api-key"] = key
        out["anthropic-version"] = lower.get("anthropic-version") or "2023-06-01"
        if lower.get("anthropic-beta"):
            out["anthropic-beta"] = lower["anthropic-beta"]
    else:
        auth = lower.get("authorization")
        if auth:
            out["authorization"] = auth
        elif lower.get("x-api-key"):
            # 兼容用 x-api-key 打 OpenAI 端点的客户端
            out["authorization"] = f"Bearer {lower['x-api-key']}"
    out.setdefault("content-type", "application/json")
    return out


def _bearer(auth: str | None) -> str:
    if not auth:
        return ""
    parts = auth.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return auth.strip()


def filter_response_headers(headers: httpx.Headers, secret: str = "") -> dict[str, str]:
    """白名单透传响应头；若 value 反射了客户端 key 则丢弃。"""
    out: dict[str, str] = {}
    for k, v in headers.items():
        lk = k.lower()
        if not (lk in _RESP_HEADER_ALLOW or lk.startswith(_RESP_HEADER_PREFIX)):
            continue
        if secret and secret in v:
            continue
        out[k] = v
    return out


class SecretFilter:
    """滚动窗口凭证脱敏：处理密钥被拆到两个 chunk 的情况。"""

    def __init__(self, secret: str, replacement: str = "[redacted]") -> None:
        self.secret = secret or ""
        self.replacement = replacement
        self._buf = ""

    def feed(self, chunk: str) -> str:
        if not self.secret:
            return chunk
        data = self._buf + chunk
        # 保留尾部可能被截断的部分
        keep = len(self.secret) - 1
        if keep <= 0:
            return data.replace(self.secret, self.replacement)
        head, tail = data[:-keep], data[-keep:]
        self._buf = tail
        return head.replace(self.secret, self.replacement)

    def finish(self) -> str:
        data, self._buf = self._buf, ""
        return data.replace(self.secret, self.replacement) if self.secret else data


def redact_text(text: str, secret: str) -> str:
    if not secret:
        return text
    return text.replace(secret, "[redacted]")


def extract_usage(obj: dict[str, Any]) -> dict[str, Any] | None:
    usage = obj.get("usage")
    return usage if isinstance(usage, dict) else None


class SSEObserver:
    """旁路只读观测：统计 usage / 首 token / 终止事件。绝不改写内容。"""

    def __init__(self) -> None:
        self._buf = ""
        self.first_token_ms: int | None = None
        self.usage: dict[str, Any] | None = None
        self.finished = False
        self._start = None

    def start(self, ts: float) -> None:
        self._start = ts

    def feed(self, chunk: bytes, now_ts: float) -> None:
        try:
            text = chunk.decode("utf-8", errors="ignore")
        except Exception:
            return
        if self.first_token_ms is None and self._start is not None and text.strip():
            self.first_token_ms = int((now_ts - self._start) * 1000)
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._handle_line(line.strip())

    def _handle_line(self, line: str) -> None:
        if not line.startswith("data:"):
            return
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            if payload == "[DONE]":
                self.finished = True
            return
        try:
            obj = json.loads(payload)
        except ValueError:
            return
        if not isinstance(obj, dict):
            return
        usage = extract_usage(obj)
        if usage:
            self.usage = usage
        etype = obj.get("type")
        if etype in ("message_stop", "response.completed"):
            self.finished = True
        msg = obj.get("message")
        if isinstance(msg, dict):
            u = extract_usage(msg)
            if u:
                self.usage = u
        for choice in obj.get("choices") or []:
            if isinstance(choice, dict) and choice.get("finish_reason"):
                self.finished = True


def make_client() -> httpx.AsyncClient:
    """流式场景限制 keepalive，避免连接复用脏读。"""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=10.0,
            read=float(config.REQUEST_TIMEOUT),
            write=30.0,
            pool=30.0,
        ),
        limits=httpx.Limits(
            max_connections=50,
            max_keepalive_connections=2,
            keepalive_expiry=30.0,
        ),
        follow_redirects=False,
    )


async def stream_passthrough(
    upstream: httpx.Response,
    observer: SSEObserver,
    secret_filter: SecretFilter,
    now_fn,
) -> AsyncIterator[bytes]:
    """逐 chunk 原样透传，旁路只读观测，绝不重组重发。"""
    try:
        async for chunk in upstream.aiter_bytes():
            observer.feed(chunk, now_fn())
            if secret_filter.secret:
                # 仅在命中密钥时才做字节级替换，避免破坏正常 SSE 边界
                if secret_filter.secret.encode() in chunk:
                    chunk = chunk.replace(
                        secret_filter.secret.encode(),
                        secret_filter.replacement.encode(),
                    )
            yield chunk
    finally:
        await upstream.aclose()
