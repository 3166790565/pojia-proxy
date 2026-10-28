"""多上游路由测试：body_param / key 模式。"""

from app import db, selector, seed_builder


def _upstreams():
    return [
        {"id": 1, "name": "default", "base_url": "https://default.example.com", "enabled": 1, "priority": 100},
        {"id": 2, "name": "orb", "base_url": "https://orb.example.com", "enabled": 1, "priority": 90},
        {"id": 3, "name": "deepseek", "base_url": "https://ds.example.com", "enabled": 1, "priority": 80},
    ]


def _settings(mode="single", param="shangyou"):
    return {"upstream_mode": mode, "upstream_param": param, "upstream_base": "https://fallback.example.com"}


def _routes():
    return [
        {"id": 1, "key_match": "sk-orb-*", "upstream_id": 2, "priority": 10, "enabled": 1},
        {"id": 2, "key_match": "sk-ds-*", "upstream_id": 3, "priority": 20, "enabled": 1},
        {"id": 3, "key_match": "sk-exact-xyz", "upstream_id": 2, "priority": 30, "enabled": 1},
    ]


def test_single_mode_uses_base():
    r = selector.resolve_upstream("single", {}, "", _settings(), _upstreams(), _routes())
    assert r["base_url"] == "https://fallback.example.com"
    assert r["source"] == "single"


def test_body_param_selects_by_name():
    r = selector.resolve_upstream("body_param", {"shangyou": "orb"}, "", _settings("body_param"), _upstreams(), _routes())
    assert r["name"] == "orb"
    assert r["base_url"] == "https://orb.example.com"
    assert r["source"] == "body_param"


def test_body_param_unmatched_falls_back():
    r = selector.resolve_upstream("body_param", {"shangyou": "nonexistent"}, "", _settings("body_param"), _upstreams(), _routes())
    assert r["source"] == "fallback"
    assert r["base_url"] == "https://fallback.example.com"
    assert r["param_value"] == "nonexistent"


def test_body_param_missing_falls_back():
    r = selector.resolve_upstream("body_param", {"model": "x"}, "", _settings("body_param"), _upstreams(), _routes())
    # 未提供选择参数 → 走默认上游
    assert r["source"] == "single"
    assert r["base_url"] == "https://fallback.example.com"


def test_key_route_prefix_match():
    r = selector.resolve_upstream("key", None, "sk-orb-abcdef", _settings("key"), _upstreams(), _routes())
    assert r["name"] == "orb"
    assert r["source"] == "key"


def test_key_route_exact_match():
    r = selector.resolve_upstream("key", None, "sk-exact-xyz", _settings("key"), _upstreams(), _routes())
    assert r["name"] == "orb"


def test_key_route_no_match_falls_back():
    r = selector.resolve_upstream("key", None, "sk-unknown", _settings("key"), _upstreams(), _routes())
    assert r["source"] == "single"
    assert r["base_url"] == "https://fallback.example.com"


def test_key_route_disabled_skipped():
    routes = [{"id": 1, "key_match": "sk-orb-*", "upstream_id": 2, "priority": 10, "enabled": 0}]
    r = selector.resolve_upstream("key", None, "sk-orb-x", _settings("key"), _upstreams(), routes)
    assert r["source"] == "single"


def test_strip_upstream_param():
    body = {"model": "x", "shangyou": "orb", "messages": []}
    out = selector.strip_upstream_param(body, _settings("body_param"))
    assert "shangyou" not in out
    assert out["model"] == "x"


def test_strip_upstream_param_custom_name():
    body = {"model": "x", "up": "orb"}
    out = selector.strip_upstream_param(body, _settings("body_param", "up"))
    assert "up" not in out


def test_strip_upstream_param_no_param():
    body = {"model": "x"}
    assert selector.strip_upstream_param(body, _settings()) == body


def test_match_key_route_regex():
    routes = [{"id": 1, "key_match": "re:^sk-ds-\\d+$", "upstream_id": 3, "priority": 1, "enabled": 1}]
    assert selector.match_key_route("sk-ds-123", routes)["upstream_id"] == 3
    assert selector.match_key_route("sk-ds-abc", routes) is None


def test_seed_creates_default_upstream():
    seed_builder.ensure_all()
    names = [u["name"] for u in db.list_upstreams()]
    assert "default" in names


def test_upstream_crud():
    seed_builder.ensure_all()
    uid = db.create_upstream("test-up", "https://test.example.com", "测试", True, 50)
    assert db.get_upstream(uid)["name"] == "test-up"
    assert db.update_upstream(uid, {"base_url": "https://new.example.com"})
    assert db.get_upstream_by_name("test-up")["base_url"] == "https://new.example.com"
    assert db.delete_upstream(uid)


def test_key_route_crud():
    seed_builder.ensure_all()
    uid = db.create_upstream("kr-up", "https://kr.example.com", "", True, 50)
    rid = db.create_key_route("sk-kr-*", uid, 10, True)
    assert any(r["id"] == rid for r in db.list_key_routes())
    assert db.update_key_route(rid, {"priority": 5})
    assert db.delete_key_route(rid)
    db.delete_upstream(uid)
