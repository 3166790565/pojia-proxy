"""对外代理路由：/v1/chat/completions、/v1/messages、/v1/models。"""

from __future__ import annotations

import copy
import json
import time
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import config, db, engine, proxy, selector

router = APIRouter()


def _client(request: Request) -> httpx.AsyncClient:
    """复用应用生命周期内的共享 client（lifespan 中创建/关闭）。"""
    return request.app.state.http


def _error(message: str, status: int, source: str = "local") -> JSONResponse:
    return JSONResponse(
        {"error": {"message": message, "type": "relay_error", "source": source}},
        status_code=status,
    )


def _load_context() -> dict[str, Any]:
    """每次请求现取配置（热更新友好）。"""
    settings = db.get_settings()
    with db.connect() as conn:
        presets_rows = db.rows_to_dicts(conn.execute("SELECT * FROM presets").fetchall())
        packs_rows = db.rows_to_dicts(conn.execute("SELECT * FROM prompt_packs").fetchall())
    return {
        "settings": settings,
        "model_rules": db.list_model_rules(),
        "keywords": db.list_keywords(),
        "presets": selector.index_presets(presets_rows),
        "bindings": db.list_bindings(),
        "packs": {p["id"]: p for p in packs_rows},
        "upstreams": db.list_upstreams(),
        "key_routes": db.list_key_routes(),
    }


async def _read_json(request: Request) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    limit = int(db.get_setting("max_body_bytes", str(config.MAX_BODY_BYTES)) or config.MAX_BODY_BYTES)
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > limit:
        return None, _error(f"请求体超过上限 {limit} 字节", 413)
    raw = await request.body()
    if len(raw) > limit:
        return None, _error(f"请求体超过上限 {limit} 字节", 413)
    try:
        body = json.loads(raw)
    except ValueError:
        return None, _error("请求体不是合法 JSON", 400)
    if not isinstance(body, dict):
        return None, _error("请求体必须是 JSON 对象", 400)
    return body, None


def _prepare(
    body: dict[str, Any],
    protocol: str,
    ctx: dict[str, Any],
) -> dict[str, Any]:
    """选择预设 + 注入。返回注入结果元信息。"""
    settings = ctx["settings"]
    inject_enabled = settings.get("inject_enabled", "1") == "1"
    default_position = settings.get("inject_position", config.DEFAULT_POSITION)
    route_mode = settings.get("route_mode", "fine")
    model = str(body.get("model") or "")
    user_text = engine.extract_user_text(body, protocol)
    decision = selector.resolve(
        model=model,
        user_text=user_text,
        model_rules=ctx["model_rules"],
        keywords=ctx["keywords"],
        presets=ctx["presets"],
        inject_enabled=inject_enabled,
        default_position=default_position,
        route_mode=route_mode,
        bindings=ctx["bindings"],
        packs=ctx["packs"],
    )
    content = decision["content"]
    position = decision["position"]
    original = copy.deepcopy(body)
    injected = engine.apply_injection(body, protocol, content, position) if content else body
    return {
        "body": injected,
        "original": original,
        "model": model,
        "route_mode": route_mode,
        "family": decision["family"],
        "category": decision["category"],
        "pack_names": decision.get("pack_names", []),
        "preset_id": decision["preset"]["id"] if decision["preset"] else None,
        "position": position,
        "injected": bool(content),
        "inject_len": len(content),
    }


async def _forward(
    request: Request,
    protocol: str,
    route: str,
    method: str = "POST",
) -> Response:
    started = time.time()
    ctx = _load_context()
    settings = ctx["settings"]
    client_key = _client_key(request, protocol)

    body: dict[str, Any] | None = None
    meta: dict[str, Any] = {
        "model": "",
        "family": "",
        "category": "",
        "preset_id": None,
        "position": "",
        "injected": False,
        "inject_len": 0,
        "upstream": "",
        "upstream_source": "",
    }

    if method == "POST":
        body, err = await _read_json(request)
        if err is not None:
            return err
        assert body is not None
        # 先按原始请求体决定上游（含 upstream 选择参数），再剥离该参数
        up = selector.resolve_upstream(
            settings.get("upstream_mode", "single"),
            body,
            client_key,
            settings,
            ctx["upstreams"],
            ctx["key_routes"],
        )
        body = selector.strip_upstream_param(body, settings)
        meta = _prepare(body, protocol, ctx)
        meta["upstream"] = up["name"]
        meta["upstream_source"] = up["source"]
        payload = meta["body"]
        upstream_base = up["base_url"]
    else:
        up = selector.resolve_upstream(
            settings.get("upstream_mode", "single"),
            None,
            client_key,
            settings,
            ctx["upstreams"],
            ctx["key_routes"],
        )
        meta["upstream"] = up["name"]
        meta["upstream_source"] = up["source"]
        upstream_base = up["base_url"]
        payload = None

    try:
        url = proxy.upstream_url(upstream_base, route)
    except proxy.UpstreamConfigError as exc:
        return _error(str(exc), 500)

    headers = proxy.build_request_headers(dict(request.headers), protocol)
    secret_filter = proxy.SecretFilter(client_key)
    observer = proxy.SSEObserver()
    observer.start(started)

    client = _client(request)
    try:
        req = client.build_request(method, url, headers=headers, json=payload)
        upstream = await client.send(req, stream=True)
    except httpx.TimeoutException:
        _log(meta, protocol, started, 504, "upstream timeout", request)
        return _error("上游请求超时", 504)
    except httpx.HTTPError as exc:
        _log(meta, protocol, started, 502, f"upstream error: {exc}", request)
        return _error(f"上游连接失败: {exc}", 502)

    ctype = upstream.headers.get("content-type", "").lower()
    resp_headers = proxy.filter_response_headers(upstream.headers, client_key)

    if "text/event-stream" in ctype:
        log_id = _log(meta, protocol, started, upstream.status_code, "", request, pending=True)

        async def _wrapped():
            try:
                async for chunk in proxy.stream_passthrough(
                    upstream, observer, secret_filter, time.time
                ):
                    yield chunk
            finally:
                _finalize_stream_log(log_id, meta, upstream.status_code, started, observer)

        return StreamingResponse(
            _wrapped(),
            status_code=upstream.status_code,
            media_type="text/event-stream",
            headers=resp_headers,
        )

    try:
        data = await upstream.aread()
    finally:
        await upstream.aclose()

    text = data.decode("utf-8", errors="replace")
    if client_key:
        text = proxy.redact_text(text, client_key)
    duration = int((time.time() - started) * 1000)
    _log(meta, protocol, started, upstream.status_code, "", request, duration=duration)
    return Response(
        content=text.encode("utf-8"),
        status_code=upstream.status_code,
        headers=resp_headers,
    )


def _client_key(request: Request, protocol: str) -> str:
    h = {k.lower(): v for k, v in request.headers.items()}
    if protocol == "anthropic":
        return h.get("x-api-key") or proxy._bearer(h.get("authorization"))
    auth = h.get("authorization")
    if auth:
        return proxy._bearer(auth)
    return h.get("x-api-key", "")


def _log(
    meta: dict[str, Any],
    protocol: str,
    started: float,
    status: int,
    error: str,
    request: Request,
    duration: int | None = None,
    pending: bool = False,
) -> int | None:
    duration = duration if duration is not None else int((time.time() - started) * 1000)
    try:
        return db.insert_log(
            protocol=protocol,
            model=meta.get("model", ""),
            family=meta.get("family", ""),
            category=meta.get("category", ""),
            preset_id=meta.get("preset_id"),
            route_mode=meta.get("route_mode", ""),
            packs=meta.get("pack_names", []),
            upstream=meta.get("upstream", ""),
            upstream_source=meta.get("upstream_source", ""),
            position=meta.get("position", ""),
            injected=1 if meta.get("injected") else 0,
            inject_len=meta.get("inject_len", 0),
            status=status,
            duration_ms=duration,
            error=error,
            client_ip=(request.client.host if request.client else ""),
            request_path=request.url.path,
        )
    except Exception:
        return None


def _finalize_stream_log(
    log_id: int | None,
    meta: dict[str, Any],
    status: int,
    started: float,
    observer: proxy.SSEObserver,
) -> None:
    if log_id is None:
        return
    try:
        with db.connect() as conn:
            conn.execute(
                "UPDATE request_logs SET status=?, duration_ms=?, first_token_ms=?, usage=?, error=? WHERE id=?",
                (
                    status,
                    int((time.time() - started) * 1000),
                    observer.first_token_ms,
                    json.dumps(observer.usage, ensure_ascii=False) if observer.usage else "",
                    "",
                    log_id,
                ),
            )
    except Exception:
        pass


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    return await _forward(request, "openai", "chat/completions", "POST")


@router.post("/v1/messages")
async def messages(request: Request):
    return await _forward(request, "anthropic", "messages", "POST")


@router.get("/v1/models")
async def models(request: Request):
    # 依据请求头判断：带 x-api-key 且无 authorization 视为 Anthropic 风格
    h = {k.lower(): v for k, v in request.headers.items()}
    protocol = "anthropic" if (h.get("x-api-key") and not h.get("authorization")) else "openai"
    return await _forward(request, protocol, "models", "GET")
