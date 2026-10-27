import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# 关键：环境变量必须在 conftest 模块加载时就位（任何用例 import app.* 之前），
# 否则 app.db 的模块级 engine 会绑定到默认的 data/ontology.db（真实库）。
# 不请求 client fixture、直接调 SessionLocal 的用例曾因此写穿真实库（实例镜像被覆盖）。
_TEST_DB = Path(tempfile.mkdtemp(prefix="ontology-test-")) / "test.db"
os.environ["DB_URL"] = os.environ.get("TEST_DB_URL") or f"sqlite:///{_TEST_DB.as_posix()}"
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("SEED_DEMO", "1")
os.environ.setdefault("CSRF_ENABLED", "0")  # P0：表单测试不逐个带 token；CSRF 由 test_security_p0.py 专项覆盖


@pytest.fixture(scope="session")
def app():
    """会话级应用实例（env 已在 conftest 模块级设好；CI 可用 TEST_DB_URL 切 PostgreSQL）。"""
    from app.main import app as fastapi_app
    return fastapi_app


@pytest.fixture()
def client(app):
    """每个用例一个全新 TestClient（会话 Cookie 互不影响，共享同一个库）。"""
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


def login(client, username, password=None):
    return client.post(
        "/login",
        data={"username": username, "password": password or username},
        follow_redirects=False,
    )


def concept_id_by_name(client, name):
    for item in client.get("/api/concepts", params={"q": name}).json()["items"]:
        if item["name"] == name:
            return item["id"]
    raise AssertionError(f"概念不存在：{name}")
