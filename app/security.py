"""管理员密码哈希与会话 cookie。"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import config, db

SESSION_COOKIE = "relay_session"
SESSION_SALT = "admin-session"

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt.encode("utf-8"),
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt.encode("utf-8"),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(digest)),
        )
        return hmac.compare_digest(dk.hex(), digest)
    except (ValueError, TypeError):
        return False


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(config.load_secret_key(), salt=SESSION_SALT)


def create_session() -> str:
    return _serializer().dumps({"role": "admin", "nonce": secrets.token_hex(8)})


def read_session(token: str, max_age: int) -> dict[str, Any] | None:
    try:
        return _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


def check_admin_password(password: str) -> bool:
    """校验登录密码。优先用 DB 中的哈希，其次用环境变量（明文或哈希）。"""
    record = db.get_admin()
    if record:
        return verify_password(password, record["password_hash"])
    if config.ADMIN_PASSWORD_HASH:
        return verify_password(password, config.ADMIN_PASSWORD_HASH)
    if config.ADMIN_PASSWORD:
        return hmac.compare_digest(password, config.ADMIN_PASSWORD)
    return False


def ensure_admin_configured() -> None:
    """首次启动时把密码（明文或哈希）写入 DB；都没有则生成随机密码并打印一次。

    并发安全：多 worker 同时启动时，只有真正插入成功的一方打印密码。
    """
    if config.ADMIN_PASSWORD:
        inserted = db.set_admin_hash(hash_password(config.ADMIN_PASSWORD), only_if_missing=True)
        return
    if config.ADMIN_PASSWORD_HASH:
        inserted = db.set_admin_hash(config.ADMIN_PASSWORD_HASH, only_if_missing=True)
        return
    password = secrets.token_urlsafe(12)
    inserted = db.set_admin_hash(hash_password(password), only_if_missing=True)
    if inserted:
        print("=" * 60, flush=True)
        print("[破甲路由] 未配置管理员密码，已生成初始密码：", flush=True)
        print(f"    {password}", flush=True)
        print("请立即登录 WebUI 修改，或设置环境变量 ADMIN_PASSWORD。", flush=True)
        print("=" * 60, flush=True)
