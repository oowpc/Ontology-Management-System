import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(scope="session")
def app(tmp_path_factory):
    """会话级应用实例；DB_URL 必须在首次 import app 前设置。"""
    d = tmp_path_factory.mktemp("db")
    os.environ["DB_URL"] = f"sqlite:///{(d / 'test.db').as_posix()}"
    os.environ["SECRET_KEY"] = "test-secret"
    os.environ["SEED_DEMO"] = "1"
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
