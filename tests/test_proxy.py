"""代理转发测试：URL、header、key 透传、SSE、错误。"""

import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import Response, StreamingResponse
from fastapi.testclient import TestClient

from app import config, db, main, proxy, seed_builder


@pytest.fixture(scope="module")
def app_client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def upstream_ok(app_client):
    """替换共享 client 为 MockTransport 上游。"""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content) if request.content else None
        return httpx.Response(
            200,
            json={"id": "x", "choices": [{"message": {"content": "ok"}}]},
            headers={"x-request-id": "rid-1", "x-secret-echo": "should-not-pass"},
        )

    original = app_client.app.state.http
    app_client.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    yield captured
    app_client.app.state.http = original


def test_upstream_url_adds_v1():
    assert proxy.upstream_url("https://api.openai.com", "chat/completions") == (
        "https://api.openai.com/v1/chat/completions"
    )


def test_upstream_url_keeps_existing_v1():
    assert proxy.upstream_url("https://api.openai.com/v1", "messages") == (
        "https://api.openai.com/v1/messages"
    )


def test_upstream_url_rejects_query():
    with pytest.raises(ValueError):
        proxy.upstream_url("https://x.com?token=1", "models")


def test_upstream_url_rejects_no_scheme():
    with pytest.raises(ValueError):
        proxy.upstream_url("api.openai.com", "models")


def test_openai_key_passthrough(app_client, upstream_ok):
    app_client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-test-123"},
    )
    assert upstream_ok["headers"].get("authorization") == "Bearer sk-test-123"
    assert upstream_ok["url"].endswith("/v1/chat/completions")


def test_anthropic_key_and_version(app_client, upstream_ok):
    app_client.post(
        "/v1/messages",
        json={"model": "claude-sonnet-5", "messages": [{"role": "user", "content": "hi"}]},
        headers={"x-api-key": "sk-ant-abc"},
    )
    assert upstream_ok["headers"].get("x-api-key") == "sk-ant-abc"
    assert upstream_ok["headers"].get("anthropic-version") == "2023-06-01"


def test_anthropic_version_passthrough(app_client, upstream_ok):
    app_client.post(
        "/v1/messages",
        json={"model": "claude-sonnet-5", "messages": []},
        headers={"x-api-key": "k", "anthropic-version": "2024-01-01"},
    )
    assert upstream_ok["headers"].get("anthropic-version") == "2024-01-01"


def test_injection_applied_to_upstream_body(app_client, upstream_ok):
    seed_builder.ensure_all()
    db.put_settings({"inject_enabled": "1", "inject_position": "prepend"})
    app_client.post(
        "/v1/chat/completions",
        json={"model": "claude-sonnet-5", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer k"},
    )
    msgs = upstream_ok["body"]["messages"]
    assert msgs[0]["role"] == "system"
    assert len(msgs[0]["content"]) > 100


def test_response_header_whitelist(app_client, upstream_ok):
    r = app_client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": []},
        headers={"Authorization": "Bearer k"},
    )
    assert r.headers.get("x-request-id") == "rid-1"
    assert "x-secret-echo" not in r.headers


def test_reflected_key_dropped(app_client):
    def handler(request):
        return httpx.Response(200, json={}, headers={"x-request-id": "contains sk-abc"})

    original = app_client.app.state.http
    app_client.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        r = app_client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": []},
            headers={"Authorization": "Bearer sk-abc"},
        )
        assert "x-request-id" not in r.headers
    finally:
        app_client.app.state.http = original


def test_upstream_error_status_passthrough(app_client):
    def handler(request):
        return httpx.Response(429, json={"error": "rate limited"}, headers={"retry-after": "5"})

    original = app_client.app.state.http
    app_client.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        r = app_client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": []},
            headers={"Authorization": "Bearer k"},
        )
        assert r.status_code == 429
        assert r.headers.get("retry-after") == "5"
    finally:
        app_client.app.state.http = original


def test_connection_error_502(app_client):
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    original = app_client.app.state.http
    app_client.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        r = app_client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": []},
            headers={"Authorization": "Bearer k"},
        )
        assert r.status_code == 502
    finally:
        app_client.app.state.http = original


def test_bad_json_400(app_client):
    r = app_client.post(
        "/v1/chat/completions",
        content=b"not json",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400


def test_models_passthrough(app_client, upstream_ok):
    r = app_client.get("/v1/models", headers={"Authorization": "Bearer k"})
    assert r.status_code == 200
    assert upstream_ok["url"].endswith("/v1/models")


def test_sse_chunks_passthrough(app_client):
    chunks = [
        b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"b"},"finish_reason":"stop"}]}\n\n',
        b"data: [DONE]\n\n",
    ]

    def handler(request):
        async def gen():
            for c in chunks:
                yield c

        return httpx.Response(
            200,
            content=gen(),
            headers={"content-type": "text/event-stream"},
        )

    original = app_client.app.state.http
    app_client.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with app_client.stream(
            "POST",
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": []},
            headers={"Authorization": "Bearer k"},
        ) as r:
            got = b"".join(r.iter_bytes())
        assert got == b"".join(chunks)
    finally:
        app_client.app.state.http = original
