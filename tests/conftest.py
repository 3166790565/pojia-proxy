import os
import tempfile
import sys
from pathlib import Path

import pytest

# 隔离数据目录（在导入 app 前设置）
_tmp = tempfile.mkdtemp(prefix="relay_test_")
os.environ["DATA_DIR"] = _tmp
os.environ["DB_PATH"] = str(Path(_tmp) / "relay.db")
os.environ["PRESET_DIR"] = str(Path(_tmp) / "presets")
os.environ["SECRET_KEY"] = "test-secret-key"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402

config.DATA_DIR = Path(_tmp)
config.DB_PATH = Path(_tmp) / "relay.db"
config.PRESET_DIR = Path(_tmp) / "presets"
config.SECRET_KEY = "test-secret-key"


@pytest.fixture(autouse=True)
def fresh_db():
    from app import db

    db.init_db()
    with db.connect() as conn:
        db.ensure_default_settings(conn)
    yield
