"""管理接口测试：登录、CRUD、presets 懒加载契约、日志。"""

import pytest
from fastapi.testclient import TestClient

from app import db, main, security, seed_builder


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def admin(client):
    db.set_admin_hash(security.hash_password("pw123"))
    r = client.post("/admin/login", json={"password": "pw123"})
    assert r.status_code == 200
    yield client
    client.post("/admin/logout")


def test_login_wrong_password(client):
    db.set_admin_hash(security.hash_password("pw123"))
    r = client.post("/admin/login", json={"password": "bad"})
    assert r.status_code == 401


def test_admin_requires_auth(client):
    client.cookies.clear()
    assert client.get("/admin/settings").status_code == 401


def test_health_open(client):
    assert client.get("/admin/health").status_code == 200


def test_settings_roundtrip(admin):
    r = admin.put("/admin/settings", json={"values": {"inject_position": "append"}})
    assert r.status_code == 200
    assert admin.get("/admin/settings").json()["inject_position"] == "append"


def test_settings_rejects_bad_position(admin):
    r = admin.put("/admin/settings", json={"values": {"inject_position": "middle"}})
    assert r.status_code == 400


def test_model_rule_crud(admin):
    rid = admin.post(
        "/admin/model_rules",
        json={"family": "test", "pattern": "test-*", "priority": 5},
    ).json()["id"]
    assert any(r["id"] == rid for r in admin.get("/admin/model_rules").json())
    assert admin.put(f"/admin/model_rules/{rid}", json={"priority": 1}).status_code == 200
    assert admin.delete(f"/admin/model_rules/{rid}").status_code == 200


def test_keyword_crud(admin):
    kid = admin.post(
        "/admin/keywords",
        json={"category": "逆向", "keyword": "调试 断点", "priority": 5},
    ).json()["id"]
    assert any(k["id"] == kid for k in admin.get("/admin/keywords").json())
    assert admin.delete(f"/admin/keywords/{kid}").status_code == 200


def test_presets_summary_has_no_full_content(admin):
    seed_builder.ensure_all()
    data = admin.get("/admin/presets").json()
    assert len(data["items"]) == 28
    assert "families" in data and "categories" in data
    for item in data["items"]:
        assert "content" not in item
        assert "preview" in item
        assert item["content_len"] > 0


def test_preset_detail_has_full_content(admin):
    seed_builder.ensure_all()
    items = admin.get("/admin/presets").json()["items"]
    pid = items[0]["id"]
    detail = admin.get(f"/admin/presets/{pid}").json()
    assert "content" in detail
    assert len(detail["content"]) > 50


def test_preset_update_and_position(admin):
    seed_builder.ensure_all()
    items = admin.get("/admin/presets").json()["items"]
    pid = items[0]["id"]
    r = admin.put(f"/admin/presets/{pid}", json={"content": "NEW", "position": "append"})
    assert r.status_code == 200
    detail = admin.get(f"/admin/presets/{pid}").json()
    assert detail["content"] == "NEW"
    assert detail["position"] == "append"


def test_preset_bad_position(admin):
    seed_builder.ensure_all()
    pid = admin.get("/admin/presets").json()["items"][0]["id"]
    assert admin.put(f"/admin/presets/{pid}", json={"position": "bad"}).status_code == 400


def test_copy_row(admin):
    seed_builder.ensure_all()
    items = admin.get("/admin/presets").json()["items"]
    src = next(i for i in items if i["family"] == "other" and i["category"] == "通用")
    r = admin.post("/admin/presets/copy", json={"src_id": src["id"], "scope": "row"})
    assert r.status_code == 200
    assert r.json()["affected"] == 3


def test_logs_pagination(admin):
    for i in range(25):
        db.insert_log(protocol="openai", model="gpt-4o", status=200, injected=1)
    page = admin.get("/admin/logs?page=1&page_size=20").json()
    assert page["total"] >= 25
    assert len(page["items"]) == 20
    page2 = admin.get("/admin/logs?page=2&page_size=20").json()
    assert len(page2["items"]) >= 5


def test_clear_logs(admin):
    db.insert_log(protocol="openai", model="x", status=200)
    assert admin.delete("/admin/logs").json()["deleted"] >= 1


def test_packs_crud(admin):
    pid = admin.post("/admin/packs", json={"name": "测试包", "content": "内容内容", "enabled": True}).json()["id"]
    assert any(p["id"] == pid for p in admin.get("/admin/packs").json())
    assert admin.put(f"/admin/packs/{pid}", json={"content": "新内容"}).status_code == 200
    assert admin.get(f"/admin/packs/{pid}").json()["content"] == "新内容"
    assert admin.delete(f"/admin/packs/{pid}").status_code == 200


def test_bindings_crud(admin):
    pid = admin.post("/admin/packs", json={"name": "绑定包", "content": "x", "enabled": True}).json()["id"]
    bid = admin.post("/admin/bindings", json={"pattern": "deepseek-*", "pack_id": pid, "priority": 5}).json()["id"]
    rows = admin.get("/admin/bindings").json()
    assert any(b["id"] == bid and b["pack_name"] == "绑定包" for b in rows)
    assert admin.put(f"/admin/bindings/{bid}", json={"priority": 1}).status_code == 200
    assert admin.delete(f"/admin/bindings/{bid}").status_code == 200
    admin.delete(f"/admin/packs/{pid}")


def test_route_mode_setting(admin):
    r = admin.put("/admin/settings", json={"values": {"route_mode": "pack"}})
    assert r.status_code == 200
    assert admin.get("/admin/settings").json()["route_mode"] == "pack"
    assert admin.put("/admin/settings", json={"values": {"route_mode": "bad"}}).status_code == 400


def test_binding_bad_pack_rejected(admin):
    assert admin.post("/admin/bindings", json={"pattern": "x-*", "pack_id": 99999}).status_code == 400
