"""WebUI 后端：登录、设置、规则、关键词、预设、日志。"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import config, db, security, selector, seed_builder

router = APIRouter(prefix="/admin")


# ---------------- 鉴权 ----------------

def require_admin(request: Request) -> None:
    token = request.cookies.get(security.SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    ttl = int(db.get_setting("session_ttl", str(config.SESSION_TTL)) or config.SESSION_TTL)
    if security.read_session(token, ttl) is None:
        raise HTTPException(status_code=401, detail="会话无效或已过期")


class LoginBody(BaseModel):
    password: str = ""


@router.post("/login")
async def login(body: LoginBody, response: Response):
    if not security.check_admin_password(body.password):
        raise HTTPException(status_code=401, detail="密码错误")
    token = security.create_session()
    ttl = int(db.get_setting("session_ttl", str(config.SESSION_TTL)) or config.SESSION_TTL)
    response.set_cookie(
        security.SESSION_COOKIE,
        token,
        max_age=ttl,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(security.SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me(_: None = Depends(require_admin)):
    return {"ok": True, "role": "admin"}


@router.get("/health")
async def health():
    return {"ok": True, "ts": int(time.time())}


# ---------------- 设置 ----------------

_SETTING_KEYS = {
    "upstream_base",
    "inject_enabled",
    "inject_position",
    "request_timeout",
    "max_body_bytes",
    "log_retention_days",
    "session_ttl",
    "cors_allow",
}


@router.get("/settings")
async def get_settings(_: None = Depends(require_admin)):
    return db.get_settings()


class SettingsBody(BaseModel):
    values: dict[str, Any]


@router.put("/settings")
async def put_settings(body: SettingsBody, _: None = Depends(require_admin)):
    clean: dict[str, Any] = {}
    for k, v in body.values.items():
        if k not in _SETTING_KEYS:
            continue
        if k == "inject_position" and v not in config.POSITIONS:
            raise HTTPException(400, "inject_position 非法")
        if k == "upstream_base":
            v = str(v).strip()
            if v:
                try:
                    from .proxy import upstream_url

                    upstream_url(v, "models")
                except ValueError as exc:
                    raise HTTPException(400, f"upstream_base 非法: {exc}")
        clean[k] = v
    db.put_settings(clean)
    return {"ok": True, "updated": list(clean)}


# ---------------- 模型规则 ----------------

class RuleBody(BaseModel):
    family: str = Field(min_length=1)
    pattern: str = Field(min_length=1)
    priority: int = 100
    enabled: bool = True
    description: str = ""


@router.get("/model_rules")
async def list_rules(q: str = "", family: str = "", _: None = Depends(require_admin)):
    rows = db.list_model_rules()
    needle = q.strip().lower()
    if family:
        rows = [r for r in rows if r["family"] == family]
    if needle:
        rows = [
            r
            for r in rows
            if needle in f"{r['family']} {r['pattern']} {r['description']}".lower()
        ]
    return rows


@router.post("/model_rules")
async def create_rule(body: RuleBody, _: None = Depends(require_admin)):
    rid = db.create_model_rule(body.family, body.pattern, body.priority, body.enabled, body.description)
    return {"id": rid}


@router.put("/model_rules/{rule_id}")
async def update_rule(rule_id: int, body: dict[str, Any], _: None = Depends(require_admin)):
    if not db.update_model_rule(rule_id, body):
        raise HTTPException(404, "规则不存在或无变更")
    return {"ok": True}


@router.delete("/model_rules/{rule_id}")
async def delete_rule(rule_id: int, _: None = Depends(require_admin)):
    if not db.delete_model_rule(rule_id):
        raise HTTPException(404, "规则不存在")
    return {"ok": True}


# ---------------- 关键词 ----------------

class KeywordBody(BaseModel):
    category: str = Field(min_length=1)
    keyword: str = Field(min_length=1)
    priority: int = 100
    enabled: bool = True


@router.get("/keywords")
async def list_keywords(q: str = "", category: str = "", _: None = Depends(require_admin)):
    rows = db.list_keywords()
    needle = q.strip().lower()
    if category:
        rows = [r for r in rows if r["category"] == category]
    if needle:
        rows = [r for r in rows if needle in f"{r['category']} {r['keyword']}".lower()]
    return rows


@router.post("/keywords")
async def create_keyword(body: KeywordBody, _: None = Depends(require_admin)):
    kid = db.create_keyword(body.category, body.keyword, body.priority, body.enabled)
    return {"id": kid}


@router.put("/keywords/{keyword_id}")
async def update_keyword(keyword_id: int, body: dict[str, Any], _: None = Depends(require_admin)):
    if not db.update_keyword(keyword_id, body):
        raise HTTPException(404, "关键词不存在或无变更")
    return {"ok": True}


@router.delete("/keywords/{keyword_id}")
async def delete_keyword(keyword_id: int, _: None = Depends(require_admin)):
    if not db.delete_keyword(keyword_id):
        raise HTTPException(404, "关键词不存在")
    return {"ok": True}


# ---------------- 预设 ----------------

@router.get("/presets")
async def list_presets(q: str = "", family: str = "", category: str = "", _: None = Depends(require_admin)):
    """精简列表：不含全文，仅 preview。"""
    return {
        "families": list(seed_builder.FAMILIES),
        "categories": list(selector.CATEGORIES),
        "items": db.list_presets_summary(q=q, family=family, category=category),
    }


@router.get("/presets/{preset_id}")
async def get_preset(preset_id: int, _: None = Depends(require_admin)):
    row = db.get_preset(preset_id)
    if row is None:
        raise HTTPException(404, "预设不存在")
    return row


class PresetBody(BaseModel):
    content: str | None = None
    position: str | None = None
    enabled: bool | None = None
    priority: int | None = None


@router.put("/presets/{preset_id}")
async def update_preset(preset_id: int, body: PresetBody, _: None = Depends(require_admin)):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "position" in fields and fields["position"] not in config.POSITIONS:
        raise HTTPException(400, "position 非法")
    if not db.update_preset(preset_id, fields):
        raise HTTPException(404, "预设不存在或无变更")
    return {"ok": True}


class CopyBody(BaseModel):
    src_id: int
    scope: str = Field(pattern="^(row|col)$")


@router.post("/presets/copy")
async def copy_preset(body: CopyBody, _: None = Depends(require_admin)):
    n = db.copy_preset(body.src_id, body.scope, "")
    return {"ok": True, "affected": n}


# ---------------- 日志 ----------------

@router.get("/logs")
async def logs(
    page: int = 1,
    page_size: int = 20,
    model: str = "",
    family: str = "",
    category: str = "",
    status: int | None = None,
    injected: int | None = None,
    _: None = Depends(require_admin),
):
    page_size = max(1, min(page_size, 200))
    return db.query_logs(
        page=max(1, page),
        page_size=page_size,
        model=model,
        family=family,
        category=category,
        status=status,
        injected=injected,
    )


@router.delete("/logs")
async def clear_logs(_: None = Depends(require_admin)):
    n = db.clear_logs()
    return {"ok": True, "deleted": n}
