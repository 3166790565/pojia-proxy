"""SQLite 存储层：建表、迁移、CRUD 辅助。"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS admin (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    password_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS model_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    family TEXT NOT NULL,
    pattern TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    enabled INTEGER NOT NULL DEFAULT 1,
    description TEXT NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    keyword TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    family TEXT NOT NULL,
    category TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    position TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    priority INTEGER NOT NULL DEFAULT 100,
    updated_at INTEGER NOT NULL,
    UNIQUE (family, category)
);

-- 破甲词包：独立命名的破甲词（如 冷咖啡、小码酱、DevSec），与模型家族解耦
CREATE TABLE IF NOT EXISTS prompt_packs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at INTEGER NOT NULL
);

-- 多上游：按名称选择目标上游
CREATE TABLE IF NOT EXISTS upstreams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    base_url TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    priority INTEGER NOT NULL DEFAULT 100,
    updated_at INTEGER NOT NULL
);

-- 按密钥路由：下游 key（或其前缀）→ 上游
CREATE TABLE IF NOT EXISTS key_routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_match TEXT NOT NULL,
    upstream_id INTEGER NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at INTEGER NOT NULL
);

-- 模型 → 破甲词包绑定（可多条叠加）
CREATE TABLE IF NOT EXISTS model_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,
    pack_id INTEGER NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS request_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    protocol TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    family TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    preset_id INTEGER,
    route_mode TEXT NOT NULL DEFAULT '',
    packs TEXT NOT NULL DEFAULT '',
    upstream TEXT NOT NULL DEFAULT '',
    upstream_source TEXT NOT NULL DEFAULT '',
    position TEXT NOT NULL DEFAULT '',
    injected INTEGER NOT NULL DEFAULT 0,
    inject_len INTEGER NOT NULL DEFAULT 0,
    status INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    first_token_ms INTEGER,
    usage TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    client_ip TEXT NOT NULL DEFAULT '',
    request_path TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_logs_ts ON request_logs(ts DESC);
CREATE INDEX IF NOT EXISTS idx_logs_model ON request_logs(model);
CREATE INDEX IF NOT EXISTS idx_rules_prio ON model_rules(priority, id);
CREATE INDEX IF NOT EXISTS idx_keywords_prio ON keywords(priority, id);
"""


def now() -> int:
    return int(time.time())


def _db_path() -> Path:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return config.DB_PATH


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(_db_path()), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """为旧库补充新列（幂等）。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(request_logs)").fetchall()}
    if "route_mode" not in cols:
        conn.execute("ALTER TABLE request_logs ADD COLUMN route_mode TEXT NOT NULL DEFAULT ''")
    if "packs" not in cols:
        conn.execute("ALTER TABLE request_logs ADD COLUMN packs TEXT NOT NULL DEFAULT ''")
    if "upstream" not in cols:
        conn.execute("ALTER TABLE request_logs ADD COLUMN upstream TEXT NOT NULL DEFAULT ''")
    if "upstream_source" not in cols:
        conn.execute("ALTER TABLE request_logs ADD COLUMN upstream_source TEXT NOT NULL DEFAULT ''")
    # settings 补默认键（route_mode）
    ts = now()
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO NOTHING",
            (key, value, ts),
        )


def init_db() -> None:
    """建表 + 迁移。并发安全：executescript 加锁重试。"""
    last: Exception | None = None
    for _ in range(5):
        try:
            with connect() as conn:
                conn.executescript(SCHEMA)
                _migrate(conn)
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            last = exc
            time.sleep(0.2)
    if last:
        raise last


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


# ---------------- settings ----------------

DEFAULT_SETTINGS: dict[str, str] = {
    "upstream_base": "https://api.openai.com",
    "inject_enabled": "1",
    "inject_position": config.DEFAULT_POSITION,
    "request_timeout": str(int(config.REQUEST_TIMEOUT)),
    "max_body_bytes": str(config.MAX_BODY_BYTES),
    "log_retention_days": str(config.LOG_RETENTION_DAYS),
    "session_ttl": str(config.SESSION_TTL),
    "cors_allow": "",
    # 路由模式：fine（精细路由，模型分类+关键词）/ pack（破甲词包，按模型绑定）
    "route_mode": "fine",
    # 上游选择：single（固定 upstream_base）/ body_param（请求体参数）/ key（按下游 key）
    "upstream_mode": "single",
    # body_param 模式下的请求体参数名
    "upstream_param": "shangyou",
}


def ensure_default_settings(conn: sqlite3.Connection) -> None:
    ts = now()
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO NOTHING",
            (key, value, ts),
        )


def get_settings() -> dict[str, str]:
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    data = dict(DEFAULT_SETTINGS)
    data.update({r["key"]: r["value"] for r in rows})
    return data


def get_setting(key: str, default: str = "") -> str:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else DEFAULT_SETTINGS.get(key, default)


def put_settings(values: dict[str, Any]) -> None:
    ts = now()
    with connect() as conn:
        for key, value in values.items():
            if isinstance(value, bool):
                value = "1" if value else "0"
            conn.execute(
                "INSERT INTO settings(key, value, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, str(value), ts),
            )


# ---------------- model rules ----------------

def list_model_rules() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM model_rules ORDER BY priority ASC, id ASC"
        ).fetchall()
    return rows_to_dicts(rows)


def create_model_rule(family: str, pattern: str, priority: int, enabled: bool, description: str = "") -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO model_rules(family, pattern, priority, enabled, description, updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (family, pattern, priority, int(enabled), description, now()),
        )
        return int(cur.lastrowid)


def update_model_rule(rule_id: int, fields: dict[str, Any]) -> bool:
    allowed = {"family", "pattern", "priority", "enabled", "description"}
    sets, vals = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key}=?")
        vals.append(int(value) if key == "enabled" else value)
    if not sets:
        return False
    sets.append("updated_at=?")
    vals.append(now())
    vals.append(rule_id)
    with connect() as conn:
        cur = conn.execute(f"UPDATE model_rules SET {', '.join(sets)} WHERE id=?", vals)
        return cur.rowcount > 0


def delete_model_rule(rule_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM model_rules WHERE id=?", (rule_id,))
        return cur.rowcount > 0


# ---------------- keywords ----------------

def list_keywords() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM keywords ORDER BY priority ASC, id ASC"
        ).fetchall()
    return rows_to_dicts(rows)


def create_keyword(category: str, keyword: str, priority: int, enabled: bool) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO keywords(category, keyword, priority, enabled, updated_at) VALUES(?,?,?,?,?)",
            (category, keyword, priority, int(enabled), now()),
        )
        return int(cur.lastrowid)


def update_keyword(keyword_id: int, fields: dict[str, Any]) -> bool:
    allowed = {"category", "keyword", "priority", "enabled"}
    sets, vals = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key}=?")
        vals.append(int(value) if key == "enabled" else value)
    if not sets:
        return False
    sets.append("updated_at=?")
    vals.append(now())
    vals.append(keyword_id)
    with connect() as conn:
        cur = conn.execute(f"UPDATE keywords SET {', '.join(sets)} WHERE id=?", vals)
        return cur.rowcount > 0


def delete_keyword(keyword_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM keywords WHERE id=?", (keyword_id,))
        return cur.rowcount > 0


# ---------------- presets ----------------

def list_presets_summary(q: str = "", family: str = "", category: str = "") -> list[dict[str, Any]]:
    """精简列表：不含全文，仅 preview。"""
    sql = "SELECT id, family, category, enabled, position, priority, updated_at, content FROM presets WHERE 1=1"
    params: list[Any] = []
    if family:
        sql += " AND family=?"
        params.append(family)
    if category:
        sql += " AND category=?"
        params.append(category)
    sql += " ORDER BY family ASC, category ASC"
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    out = []
    needle = q.strip().lower()
    for r in rows:
        content = r["content"] or ""
        if needle and needle not in f"{r['family']} {r['category']} {content}".lower():
            continue
        out.append(
            {
                "id": r["id"],
                "family": r["family"],
                "category": r["category"],
                "enabled": r["enabled"],
                "position": r["position"],
                "priority": r["priority"],
                "content_len": len(content),
                "preview": content[:80],
                "updated_at": r["updated_at"],
            }
        )
    return out


def get_preset(preset_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM presets WHERE id=?", (preset_id,)).fetchone()
    return row_to_dict(row)


def get_preset_by_key(family: str, category: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM presets WHERE family=? AND category=?", (family, category)
        ).fetchone()
    return row_to_dict(row)


def upsert_preset(
    family: str,
    category: str,
    content: str,
    position: str | None = None,
    enabled: bool = True,
    priority: int = 100,
    overwrite: bool = False,
) -> int:
    """插入或更新。overwrite=False 时已存在则保留原内容（原子，并发安全）。"""
    ts = now()
    with connect() as conn:
        if overwrite:
            conn.execute(
                "INSERT INTO presets(family, category, content, position, enabled, priority, updated_at) "
                "VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(family, category) DO UPDATE SET "
                "content=excluded.content, position=excluded.position, "
                "enabled=excluded.enabled, priority=excluded.priority, updated_at=excluded.updated_at",
                (family, category, content, position, int(enabled), priority, ts),
            )
        else:
            conn.execute(
                "INSERT INTO presets(family, category, content, position, enabled, priority, updated_at) "
                "VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(family, category) DO NOTHING",
                (family, category, content, position, int(enabled), priority, ts),
            )
        row = conn.execute(
            "SELECT id FROM presets WHERE family=? AND category=?", (family, category)
        ).fetchone()
        return int(row["id"])


def update_preset(preset_id: int, fields: dict[str, Any]) -> bool:
    allowed = {"content", "position", "enabled", "priority"}
    sets, vals = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key}=?")
        if key == "enabled":
            vals.append(int(value))
        elif key == "position":
            vals.append(value if value in config.POSITIONS else None)
        else:
            vals.append(value)
    if not sets:
        return False
    sets.append("updated_at=?")
    vals.append(now())
    vals.append(preset_id)
    with connect() as conn:
        cur = conn.execute(f"UPDATE presets SET {', '.join(sets)} WHERE id=?", vals)
        return cur.rowcount > 0


def copy_preset(src_id: int, scope: str, target: str) -> int:
    """把 src 的 content 复制到整行(family)或整列(category)。返回受影响行数。"""
    with connect() as conn:
        src = conn.execute("SELECT * FROM presets WHERE id=?", (src_id,)).fetchone()
        if src is None:
            return 0
        if scope == "row":
            rows = conn.execute("SELECT id FROM presets WHERE family=?", (src["family"],)).fetchall()
        elif scope == "col":
            rows = conn.execute("SELECT id FROM presets WHERE category=?", (src["category"],)).fetchall()
        else:
            return 0
        ts = now()
        count = 0
        for r in rows:
            if r["id"] == src_id:
                continue
            conn.execute(
                "UPDATE presets SET content=?, position=?, enabled=?, updated_at=? WHERE id=?",
                (src["content"], src["position"], src["enabled"], ts, r["id"]),
            )
            count += 1
        return count


# ---------------- 破甲词包 ----------------

def list_packs(include_content: bool = False) -> list[dict[str, Any]]:
    cols = "*" if include_content else "id, name, description, enabled, updated_at, length(content) AS content_len"
    with connect() as conn:
        rows = conn.execute(f"SELECT {cols} FROM prompt_packs ORDER BY name ASC").fetchall()
    return rows_to_dicts(rows)


def get_pack(pack_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM prompt_packs WHERE id=?", (pack_id,)).fetchone()
    return row_to_dict(row)


def get_pack_by_name(name: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM prompt_packs WHERE name=?", (name,)).fetchone()
    return row_to_dict(row)


def create_pack(name: str, description: str = "", content: str = "", enabled: bool = True) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO prompt_packs(name, description, content, enabled, updated_at) VALUES(?,?,?,?,?)",
            (name, description, content, int(enabled), now()),
        )
        return int(cur.lastrowid)


def update_pack(pack_id: int, fields: dict[str, Any]) -> bool:
    allowed = {"name", "description", "content", "enabled"}
    sets, vals = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key}=?")
        vals.append(int(value) if key == "enabled" else value)
    if not sets:
        return False
    sets.append("updated_at=?")
    vals.append(now())
    vals.append(pack_id)
    with connect() as conn:
        cur = conn.execute(f"UPDATE prompt_packs SET {', '.join(sets)} WHERE id=?", vals)
        return cur.rowcount > 0


def delete_pack(pack_id: int) -> bool:
    with connect() as conn:
        conn.execute("DELETE FROM model_bindings WHERE pack_id=?", (pack_id,))
        cur = conn.execute("DELETE FROM prompt_packs WHERE id=?", (pack_id,))
        return cur.rowcount > 0


# ---------------- 模型绑定 ----------------

def list_bindings() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT b.*, p.name AS pack_name, p.enabled AS pack_enabled "
            "FROM model_bindings b LEFT JOIN prompt_packs p ON p.id = b.pack_id "
            "ORDER BY b.priority ASC, b.id ASC"
        ).fetchall()
    return rows_to_dicts(rows)


def create_binding(pattern: str, pack_id: int, priority: int = 100, enabled: bool = True) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO model_bindings(pattern, pack_id, priority, enabled, updated_at) VALUES(?,?,?,?,?)",
            (pattern, pack_id, priority, int(enabled), now()),
        )
        return int(cur.lastrowid)


def update_binding(binding_id: int, fields: dict[str, Any]) -> bool:
    allowed = {"pattern", "pack_id", "priority", "enabled"}
    sets, vals = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key}=?")
        vals.append(int(value) if key in ("enabled", "pack_id") else value)
    if not sets:
        return False
    sets.append("updated_at=?")
    vals.append(now())
    vals.append(binding_id)
    with connect() as conn:
        cur = conn.execute(f"UPDATE model_bindings SET {', '.join(sets)} WHERE id=?", vals)
        return cur.rowcount > 0


def delete_binding(binding_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM model_bindings WHERE id=?", (binding_id,))
        return cur.rowcount > 0


# ---------------- 多上游 ----------------

def list_upstreams() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM upstreams ORDER BY priority ASC, id ASC").fetchall()
    return rows_to_dicts(rows)


def get_upstream(upstream_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM upstreams WHERE id=?", (upstream_id,)).fetchone()
    return row_to_dict(row)


def get_upstream_by_name(name: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM upstreams WHERE name=?", (name,)).fetchone()
    return row_to_dict(row)


def create_upstream(name: str, base_url: str, description: str = "", enabled: bool = True, priority: int = 100) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO upstreams(name, base_url, description, enabled, priority, updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (name, base_url, description, int(enabled), priority, now()),
        )
        return int(cur.lastrowid)


def update_upstream(upstream_id: int, fields: dict[str, Any]) -> bool:
    allowed = {"name", "base_url", "description", "enabled", "priority"}
    sets, vals = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key}=?")
        vals.append(int(value) if key == "enabled" else value)
    if not sets:
        return False
    sets.append("updated_at=?")
    vals.append(now())
    vals.append(upstream_id)
    with connect() as conn:
        cur = conn.execute(f"UPDATE upstreams SET {', '.join(sets)} WHERE id=?", vals)
        return cur.rowcount > 0


def delete_upstream(upstream_id: int) -> bool:
    with connect() as conn:
        conn.execute("DELETE FROM key_routes WHERE upstream_id=?", (upstream_id,))
        cur = conn.execute("DELETE FROM upstreams WHERE id=?", (upstream_id,))
        return cur.rowcount > 0


# ---------------- 按密钥路由 ----------------

def list_key_routes() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT k.*, u.name AS upstream_name FROM key_routes k "
            "LEFT JOIN upstreams u ON u.id = k.upstream_id "
            "ORDER BY k.priority ASC, k.id ASC"
        ).fetchall()
    return rows_to_dicts(rows)


def create_key_route(key_match: str, upstream_id: int, priority: int = 100, enabled: bool = True) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO key_routes(key_match, upstream_id, priority, enabled, updated_at) VALUES(?,?,?,?,?)",
            (key_match, upstream_id, priority, int(enabled), now()),
        )
        return int(cur.lastrowid)


def update_key_route(route_id: int, fields: dict[str, Any]) -> bool:
    allowed = {"key_match", "upstream_id", "priority", "enabled"}
    sets, vals = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key}=?")
        vals.append(int(value) if key in ("enabled", "upstream_id") else value)
    if not sets:
        return False
    sets.append("updated_at=?")
    vals.append(now())
    vals.append(route_id)
    with connect() as conn:
        cur = conn.execute(f"UPDATE key_routes SET {', '.join(sets)} WHERE id=?", vals)
        return cur.rowcount > 0


def delete_key_route(route_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM key_routes WHERE id=?", (route_id,))
        return cur.rowcount > 0


# ---------------- logs ----------------

def insert_log(**fields: Any) -> int:
    cols = [
        "ts", "protocol", "model", "family", "category", "preset_id", "route_mode",
        "packs", "upstream", "upstream_source", "position", "injected", "inject_len",
        "status", "duration_ms", "first_token_ms", "usage", "error", "client_ip", "request_path",
    ]
    data = {c: fields.get(c) for c in cols}
    data["ts"] = data["ts"] or now()
    data["usage"] = json.dumps(data["usage"], ensure_ascii=False) if isinstance(data["usage"], (dict, list)) else (data["usage"] or "")
    data["error"] = data["error"] or ""
    # NOT NULL TEXT 列补默认空串
    for text_col in (
        "protocol", "model", "family", "category", "route_mode", "packs",
        "upstream", "upstream_source", "position", "client_ip", "request_path",
    ):
        if data[text_col] is None:
            data[text_col] = ""
    if isinstance(data.get("packs"), (list, tuple)):
        data["packs"] = ",".join(str(x) for x in data["packs"])
    for int_col in ("injected", "inject_len", "status", "duration_ms"):
        if data[int_col] is None:
            data[int_col] = 0
    values = [data[c] for c in cols]
    with connect() as conn:
        cur = conn.execute(
            f"INSERT INTO request_logs({', '.join(cols)}) VALUES({', '.join('?' for _ in cols)})",
            values,
        )
        return int(cur.lastrowid)


def query_logs(
    page: int = 1,
    page_size: int = 20,
    model: str = "",
    family: str = "",
    category: str = "",
    status: int | None = None,
    injected: int | None = None,
) -> dict[str, Any]:
    where = ["1=1"]
    params: list[Any] = []
    if model:
        where.append("model LIKE ?")
        params.append(f"%{model}%")
    if family:
        where.append("family=?")
        params.append(family)
    if category:
        where.append("category=?")
        params.append(category)
    if status is not None:
        where.append("status=?")
        params.append(status)
    if injected is not None:
        where.append("injected=?")
        params.append(injected)
    clause = " AND ".join(where)
    offset = max(0, (page - 1) * page_size)
    with connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS n FROM request_logs WHERE {clause}", params).fetchone()["n"]
        rows = conn.execute(
            f"SELECT * FROM request_logs WHERE {clause} ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
            [*params, page_size, offset],
        ).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        usage = d.get("usage") or ""
        if usage:
            try:
                d["usage"] = json.loads(usage)
            except (ValueError, TypeError):
                pass
        items.append(d)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def clear_logs() -> int:
    with connect() as conn:
        cur = conn.execute("DELETE FROM request_logs")
        return cur.rowcount


def purge_old_logs(days: int) -> int:
    if days <= 0:
        return 0
    cutoff = now() - days * 86400
    with connect() as conn:
        cur = conn.execute("DELETE FROM request_logs WHERE ts < ?", (cutoff,))
        return cur.rowcount


# ---------------- admin ----------------

def get_admin() -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM admin WHERE id=1").fetchone()
    return row_to_dict(row)


def set_admin_hash(password_hash: str, only_if_missing: bool = False) -> bool:
    """写入管理员哈希。only_if_missing=True 时并发安全，返回是否实际插入。"""
    ts = now()
    with connect() as conn:
        if only_if_missing:
            cur = conn.execute(
                "INSERT INTO admin(id, password_hash, created_at, updated_at) VALUES(1,?,?,?) "
                "ON CONFLICT(id) DO NOTHING",
                (password_hash, ts, ts),
            )
            return cur.rowcount > 0
        conn.execute(
            "INSERT INTO admin(id, password_hash, created_at, updated_at) VALUES(1,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET password_hash=excluded.password_hash, updated_at=excluded.updated_at",
            (password_hash, ts, ts),
        )
        return True
