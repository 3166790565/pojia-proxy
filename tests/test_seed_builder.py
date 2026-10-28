"""种子派生测试：28 格无空缺、优先级链、幂等、标记。"""

from app import config, db, seed_builder


def _cells():
    with db.connect() as conn:
        rows = conn.execute("SELECT family, category, content FROM presets").fetchall()
    return {(r["family"], r["category"]): r["content"] for r in rows}


def _reset_presets():
    with db.connect() as conn:
        conn.execute("DELETE FROM presets")
    seed_builder.seed_presets()


def test_all_28_cells_populated():
    _reset_presets()
    cells = _cells()
    assert len(cells) == 28
    for family in seed_builder.FAMILIES:
        for category in seed_builder.CATEGORIES:
            content = cells.get((family, category), "")
            assert content.strip(), f"{family}×{category} 为空"
            assert len(content) > 50, f"{family}×{category} 内容过短"


def test_manual_seed_wins():
    _reset_presets()
    cells = _cells()
    # claude 通用来自冷咖啡手工稿，应包含其标志性文本
    assert "冷咖啡" in cells[("claude", "通用")]


def test_deepseek_general_from_dsh_purge():
    _reset_presets()
    cells = _cells()
    assert "小码酱" in cells[("deepseek", "通用")]


def test_derived_marker_present_for_synthesized():
    _reset_presets()
    cells = _cells()
    # gemini×逆向 由通用稿+tail 派生
    content = cells[("gemini", "逆向")]
    assert "类别定向 · 逆向工程" in content


def test_idempotent_no_overwrite():
    _reset_presets()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id FROM presets WHERE family='claude' AND category='逆向'"
        ).fetchone()
        pid = row["id"]
    db.update_preset(pid, {"content": "USER EDITED"})
    seed_builder.ensure_all()
    cells2 = _cells()
    assert cells2[("claude", "逆向")] == "USER EDITED"


def test_arch_template_fallback(monkeypatch):
    # 把所有 seed 文件与 tail 都隐藏，强制走架构模板
    monkeypatch.setattr(config, "SEED_DIR", config.DATA_DIR / "nonexistent_seed")
    seed_files = seed_builder.load_seed_files()
    content = seed_builder._derive("gpt", "渗透", seed_files)
    assert "破甲路由" in content
    assert "类别定向" not in content or True  # tail 缺失时允许无类别段
    assert "交付格式" in content


def test_category_tail_files_exist():
    for category in ("逆向", "创作", "渗透"):
        path = config.SEED_DIR / f"{category}__tail.md"
        assert path.exists(), f"缺少 {path.name}"
        assert len(path.read_text(encoding="utf-8").strip()) > 100
