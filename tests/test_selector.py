"""选择器测试：模型分类 / 关键词匹配 / 预设组合。"""

from app import selector


def _rules():
    return [
        {"family": "claude", "pattern": "claude-*", "priority": 10, "enabled": 1, "id": 1},
        {"family": "gpt", "pattern": "gpt-*", "priority": 20, "enabled": 1, "id": 2},
        {"family": "deepseek", "pattern": "re:^deepseek-", "priority": 30, "enabled": 1, "id": 3},
        {"family": "gemini", "pattern": "GEMINI-*", "priority": 40, "enabled": 1, "id": 4},
    ]


def test_classify_glob():
    assert selector.classify_model("claude-sonnet-5", _rules()) == "claude"


def test_classify_regex():
    assert selector.classify_model("deepseek-v4-flash", _rules()) == "deepseek"


def test_classify_case_insensitive():
    assert selector.classify_model("GEMINI-3-FLASH", _rules()) == "gemini"


def test_classify_unmatched_is_other():
    assert selector.classify_model("unknown-1", _rules()) == "other"


def test_classify_priority_first_wins():
    rules = [
        {"family": "a", "pattern": "x-*", "priority": 50, "enabled": 1, "id": 1},
        {"family": "b", "pattern": "x-*", "priority": 10, "enabled": 1, "id": 2},
    ]
    assert selector.classify_model("x-1", rules) == "b"


def test_classify_disabled_skipped():
    rules = [
        {"family": "a", "pattern": "x-*", "priority": 10, "enabled": 0, "id": 1},
        {"family": "b", "pattern": "x-*", "priority": 20, "enabled": 1, "id": 2},
    ]
    assert selector.classify_model("x-1", rules) == "b"


def test_classify_bad_regex_skipped():
    rules = [{"family": "a", "pattern": "re:[", "priority": 10, "enabled": 1, "id": 1}]
    assert selector.classify_model("x", rules) == "other"


def _keywords():
    return [
        {"category": "逆向", "keyword": "逆向 破解 脱壳", "priority": 10, "enabled": 1, "id": 1},
        {"category": "创作", "keyword": "小说 剧本 设定", "priority": 20, "enabled": 1, "id": 2},
        {"category": "渗透", "keyword": "渗透 端口", "priority": 30, "enabled": 1, "id": 3},
    ]


def test_match_category_direct():
    assert selector.match_category("帮我破解这个软件", _keywords()) == "逆向"


def test_match_category_multiword_any():
    assert selector.match_category("写个剧本给我", _keywords()) == "创作"


def test_match_category_priority_wins():
    kws = [
        {"category": "创作", "keyword": "逆向", "priority": 5, "enabled": 1, "id": 1},
        {"category": "逆向", "keyword": "逆向", "priority": 10, "enabled": 1, "id": 2},
    ]
    assert selector.match_category("逆向", kws) == "创作"


def test_match_category_none_is_default():
    assert selector.match_category("随便说点什么", _keywords()) == "通用"


def test_match_category_empty_text():
    assert selector.match_category("", _keywords()) == "通用"


def _presets():
    rows = []
    for f, c, content in [
        ("claude", "通用", "claude-general"),
        ("claude", "逆向", "claude-rev"),
        ("deepseek", "逆向", "deepseek-rev"),
        ("other", "通用", "other-general"),
    ]:
        rows.append({"family": f, "category": c, "content": content, "enabled": 1})
    return selector.index_presets(rows)


def test_choose_exact():
    p = selector.choose_preset("claude", "逆向", _presets())
    assert p["content"] == "claude-rev"


def test_choose_fallback_other_x_category():
    # gemini×逆向缺 → other×逆向缺 → gemini×通用缺 → other×通用命中
    p = selector.choose_preset("gemini", "逆向", _presets())
    assert p["content"] == "other-general"


def test_choose_fallback_other_category_when_present():
    presets = dict(_presets())
    presets[("other", "逆向")] = {
        "family": "other",
        "category": "逆向",
        "content": "other-rev",
        "enabled": 1,
    }
    p = selector.choose_preset("gemini", "逆向", presets)
    assert p["content"] == "other-rev"


def test_choose_fallback_family_general():
    p = selector.choose_preset("claude", "渗透", _presets())
    assert p["content"] == "claude-general"


def test_choose_disabled_skipped():
    presets = {
        ("claude", "通用"): {"family": "claude", "category": "通用", "content": "x", "enabled": 0},
        ("other", "通用"): {"family": "other", "category": "通用", "content": "y", "enabled": 1},
    }
    p = selector.choose_preset("claude", "通用", presets)
    assert p["content"] == "y"


def test_resolve_full():
    presets = _presets()
    result = selector.resolve(
        model="claude-sonnet-5",
        user_text="帮我逆向一下",
        model_rules=_rules(),
        keywords=_keywords(),
        presets=presets,
        inject_enabled=True,
        default_position="prepend",
    )
    assert result["family"] == "claude"
    assert result["category"] == "逆向"
    assert result["content"] == "claude-rev"
    assert result["position"] == "prepend"


def test_resolve_inject_disabled():
    result = selector.resolve(
        model="claude-sonnet-5",
        user_text="x",
        model_rules=_rules(),
        keywords=_keywords(),
        presets=_presets(),
        inject_enabled=False,
        default_position="prepend",
    )
    assert result["content"] == ""
    assert result["preset"] is None


def test_resolve_preset_position_override():
    presets = _presets()
    presets[("claude", "逆向")]["position"] = "append"
    result = selector.resolve(
        model="claude-sonnet-5",
        user_text="逆向",
        model_rules=_rules(),
        keywords=_keywords(),
        presets=presets,
        inject_enabled=True,
        default_position="prepend",
    )
    assert result["position"] == "append"
