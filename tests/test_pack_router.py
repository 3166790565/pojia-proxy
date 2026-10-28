"""包路由测试：模型绑定 → 破甲词包，可叠加。"""

from app import db, seed_builder, selector


def _bindings():
    return [
        {"id": 1, "pattern": "deepseek-*", "pack_id": 1, "priority": 10, "enabled": 1},
        {"id": 2, "pattern": "deepseek-*", "pack_id": 2, "priority": 20, "enabled": 1},
        {"id": 3, "pattern": "claude-*", "pack_id": 2, "priority": 30, "enabled": 1},
    ]


def _packs():
    return {
        1: {"id": 1, "name": "小码酱", "content": "PACK-A", "enabled": 1},
        2: {"id": 2, "name": "冷咖啡", "content": "PACK-B", "enabled": 1},
    }


def test_match_bindings_single():
    hits = selector.match_bindings("claude-sonnet-5", _bindings())
    assert [h["pack_id"] for h in hits] == [2]


def test_match_bindings_stacked_ordered_by_priority():
    hits = selector.match_bindings("deepseek-v4", _bindings())
    assert [h["pack_id"] for h in hits] == [1, 2]


def test_match_bindings_unmatched():
    assert selector.match_bindings("gpt-4o", _bindings()) == []


def test_match_bindings_regex():
    bindings = [{"id": 1, "pattern": "re:^glm-", "pack_id": 1, "priority": 1, "enabled": 1}]
    assert len(selector.match_bindings("glm-5.3", bindings)) == 1


def test_pack_mode_stacks_contents():
    r = selector.resolve(
        model="deepseek-v4", user_text="任意内容",
        model_rules=[], keywords=[], presets={},
        inject_enabled=True, default_position="prepend",
        route_mode="pack", bindings=_bindings(), packs=_packs(),
    )
    assert r["content"] == "PACK-A\n\n---\n\nPACK-B"
    assert r["pack_names"] == ["小码酱", "冷咖啡"]


def test_pack_mode_single_pack():
    r = selector.resolve(
        model="claude-sonnet-5", user_text="x",
        model_rules=[], keywords=[], presets={},
        inject_enabled=True, default_position="prepend",
        route_mode="pack", bindings=_bindings(), packs=_packs(),
    )
    assert r["content"] == "PACK-B"
    assert r["pack_names"] == ["冷咖啡"]


def test_pack_mode_ignores_keywords():
    r = selector.resolve(
        model="claude-sonnet-5", user_text="帮我逆向破解",
        model_rules=[], keywords=[{"category": "逆向", "keyword": "逆向", "priority": 1, "enabled": 1}],
        presets={}, inject_enabled=True, default_position="prepend",
        route_mode="pack", bindings=_bindings(), packs=_packs(),
    )
    assert r["category"] == ""  # 包路由不看关键词


def test_pack_mode_disabled_pack_skipped():
    packs = _packs()
    packs[2]["enabled"] = 0
    r = selector.resolve(
        model="deepseek-v4", user_text="x",
        model_rules=[], keywords=[], presets={},
        inject_enabled=True, default_position="prepend",
        route_mode="pack", bindings=_bindings(), packs=packs,
    )
    assert r["content"] == "PACK-A"


def test_pack_mode_inject_disabled():
    r = selector.resolve(
        model="deepseek-v4", user_text="x",
        model_rules=[], keywords=[], presets={},
        inject_enabled=False, default_position="prepend",
        route_mode="pack", bindings=_bindings(), packs=_packs(),
    )
    assert r["content"] == ""


def test_pack_mode_no_binding():
    r = selector.resolve(
        model="unknown-1", user_text="x",
        model_rules=[], keywords=[], presets={},
        inject_enabled=True, default_position="prepend",
        route_mode="pack", bindings=_bindings(), packs=_packs(),
    )
    assert r["content"] == ""
    assert r["pack_names"] == []


def test_seed_creates_packs_and_bindings():
    seed_builder.ensure_all()
    names = [p["name"] for p in db.list_packs()]
    assert "冷咖啡" in names and "小码酱" in names and "DevSec" in names
    bindings = db.list_bindings()
    assert any(b["pattern"] == "deepseek-*" for b in bindings)
    assert any(b["pattern"] == "claude-*" for b in bindings)


def test_seed_pack_content_nonempty():
    seed_builder.ensure_all()
    for p in db.list_packs(include_content=True):
        assert len(p["content"]) > 100, f"包 {p['name']} 内容过短"


def test_default_route_mode_is_fine():
    db.put_settings({"route_mode": "fine"})
    seed_builder.ensure_all()
    assert db.get_setting("route_mode") == "fine"
