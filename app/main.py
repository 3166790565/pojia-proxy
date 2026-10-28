"""FastAPI 应用装配与生命周期。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import admin_api, config, db, proxy, proxy_api, security, seed_builder


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.PRESET_DIR.mkdir(parents=True, exist_ok=True)
    db.init_db()
    with db.connect() as conn:
        db.ensure_default_settings(conn)
    security.ensure_admin_configured()
    seed_builder.ensure_all()
    days = int(db.get_setting("log_retention_days", "0") or 0)
    if days > 0:
        db.purge_old_logs(days)
    app.state.http = proxy.make_client()
    try:
        yield
    finally:
        await app.state.http.aclose()


app = FastAPI(title="破甲路由中转站", lifespan=lifespan)

app.include_router(admin_api.router)
app.include_router(proxy_api.router)


@app.middleware("http")
async def cors_middleware(request, call_next):
    """仅对 /v1/* 按配置开放 CORS；/admin/* 不开放。"""
    allow = db.get_setting("cors_allow", "").strip()
    origin = request.headers.get("origin", "")
    is_proxy = request.url.path.startswith("/v1/")
    if allow and is_proxy and origin and origin in [o.strip() for o in allow.split(",") if o.strip()]:
        if request.method == "OPTIONS":
            from fastapi.responses import Response

            return Response(
                status_code=204,
                headers={
                    "Access-Control-Allow-Origin": origin,
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                    "Access-Control-Allow-Headers": "*",
                    "Access-Control-Max-Age": "600",
                },
            )
        response = await call_next(request)
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        return response
    return await call_next(request)


if config.WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(config.WEB_DIR)), name="static")


@app.get("/")
async def index():
    index_file = config.WEB_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"ok": True, "service": "破甲路由中转站"}
