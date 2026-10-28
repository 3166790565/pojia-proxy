"""模型分类 + 关键词匹配 + 预设选择（纯逻辑）。"""

from __future__ import annotations

import fnmatch
import re
from typing import Any

DEFAULT_FAMILY = "other"
DEFAULT_CATEGORY = "通用"
CATEGORIES = ("通用", "逆向", "创作", "渗透")


def classify_model(model: str, rules: list[dict[str, Any]]) -> str:
    """按 priority ASC, id ASC 取第一条命中的 family。未命中 → other。

    内部自行排序，不依赖调用方传入顺序。
    """
    if not model:
        return DEFAULT_FAMILY
    ordered = sorted(
        rules, key=lambda r: (int(r.get("priority", 100)), int(r.get("id", 0)))
    )
    for rule in ordered:
        if not rule.get("enabled"):
            continue
        pattern = (rule.get("pattern") or "").strip()
        if not pattern:
            continue
        if pattern.startswith("re:"):
            try:
                if re.search(pattern[3:], model, re.IGNORECASE):
                    return rule["family"]
            except re.error:
                continue
        else:
            if fnmatch.fnmatchcase(model.lower(), pattern.lower()):
                return rule["family"]
    return DEFAULT_FAMILY


def match_category(user_text: str, keywords: list[dict[str, Any]]) -> str:
    """按 priority ASC, id ASC 取第一个命中的 keyword 的 category。未命中 → 通用。"""
    if not user_text:
        return DEFAULT_CATEGORY
    text = user_text.lower()
    best: tuple[int, int, str] | None = None
    ordered = sorted(
        keywords, key=lambda k: (int(k.get("priority", 100)), int(k.get("id", 0)))
    )
    for kw in ordered:
        if not kw.get("enabled"):
            continue
        words = [w.strip() for w in (kw.get("keyword") or "").lower().split() if w.strip()]
        if not words:
            continue
        if any(w in text for w in words):
            cand = (int(kw.get("priority", 100)), int(kw.get("id", 0)), kw["category"])
            if best is None or (cand[0], cand[1]) < (best[0], best[1]):
                best = cand
    return best[2] if best else DEFAULT_CATEGORY


def _lookup(presets: dict[tuple[str, str], dict[str, Any]], family: str, category: str) -> dict[str, Any] | None:
    p = presets.get((family, category))
    if p is None:
        return None
    if not p.get("enabled", 1):
        return None
    if not (p.get("content") or "").strip():
        return None
    return p


def choose_preset(
    family: str,
    category: str,
    presets: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any] | None:
    """三级兜底：other×cat → family×通用 → other×通用。"""
    for f, c in (
        (family, category),
        (DEFAULT_FAMILY, category),
        (family, DEFAULT_CATEGORY),
        (DEFAULT_FAMILY, DEFAULT_CATEGORY),
    ):
        hit = _lookup(presets, f, c)
        if hit is not None:
            return hit
    return None


def index_presets(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(r["family"], r["category"]): r for r in rows}


def resolve(
    model: str,
    user_text: str,
    model_rules: list[dict[str, Any]],
    keywords: list[dict[str, Any]],
    presets: dict[tuple[str, str], dict[str, Any]],
    inject_enabled: bool,
    default_position: str,
    route_mode: str = "fine",
    bindings: list[dict[str, Any]] | None = None,
    packs: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """完整选择流程。

    route_mode="fine"：模型分类 + 关键词 → 28 格预设（精细路由）
    route_mode="pack"：模型 pattern 绑定 → 破甲词包，可叠加多个（包路由）
    """
    if route_mode == "pack":
        return _resolve_pack(model, inject_enabled, default_position, bindings or [], packs or {})

    family = classify_model(model, model_rules)
    category = match_category(user_text, keywords)
    preset = choose_preset(family, category, presets) if inject_enabled else None
    if preset is None:
        return {
            "family": family,
            "category": category,
            "preset": None,
            "position": default_position,
            "content": "",
            "pack_names": [],
        }
    position = preset.get("position") or default_position
    return {
        "family": family,
        "category": category,
        "preset": preset,
        "position": position,
        "content": preset.get("content") or "",
        "pack_names": [],
    }


def match_bindings(model: str, bindings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """返回该模型命中的所有绑定，按 priority ASC, id ASC 排序。"""
    if not model:
        return []
    hits = []
    ordered = sorted(
        bindings, key=lambda b: (int(b.get("priority", 100)), int(b.get("id", 0)))
    )
    for b in ordered:
        if not b.get("enabled"):
            continue
        pattern = (b.get("pattern") or "").strip()
        if not pattern:
            continue
        if pattern.startswith("re:"):
            try:
                if re.search(pattern[3:], model, re.IGNORECASE):
                    hits.append(b)
            except re.error:
                continue
        elif fnmatch.fnmatchcase(model.lower(), pattern.lower()):
            hits.append(b)
    return hits


def _resolve_pack(
    model: str,
    inject_enabled: bool,
    default_position: str,
    bindings: list[dict[str, Any]],
    packs: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """包路由：按模型绑定叠加多个破甲词包。"""
    hits = match_bindings(model, bindings) if inject_enabled else []
    parts: list[str] = []
    names: list[str] = []
    first_pack = None
    for b in hits:
        pack = packs.get(int(b.get("pack_id", 0)))
        if not pack or not pack.get("enabled"):
            continue
        content = (pack.get("content") or "").strip()
        if not content:
            continue
        parts.append(content)
        names.append(pack.get("name") or "")
        if first_pack is None:
            first_pack = pack

    content = "\n\n---\n\n".join(parts)
    return {
        "family": "",
        "category": "",
        "preset": first_pack,
        "position": default_position,
        "content": content,
        "pack_names": names,
    }
