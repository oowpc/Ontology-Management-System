"""KE-matcher 客户端：只测 HTTP 封装，不依赖 8082 服务。"""
import io
import json

from app import ke_matcher


class _Resp:
    def __init__(self, data):
        self._data = json.dumps(data).encode()

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_judge_posts_reference_and_candidate(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data.decode())
        seen["timeout"] = timeout
        return _Resp({"status": "proved_equal"})

    monkeypatch.setattr(ke_matcher.urllib.request, "urlopen", fake_urlopen)
    out = ke_matcher.judge("output = 1;", "output = 2;", request_timeout_s=12)
    assert out["status"] == "proved_equal"
    assert seen["url"] == ke_matcher.BASE_URL + "/judge"
    assert seen["body"]["ke_a"] == "output = 1;"
    assert seen["body"]["ke_b"] == "output = 2;"
    assert seen["body"]["layers"]["s1"] is True
    assert seen["body"]["request_timeout_s"] == 12


def test_health_gets(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        return _Resp({"ok": True})

    monkeypatch.setattr(ke_matcher.urllib.request, "urlopen", fake_urlopen)
    assert ke_matcher.health()["ok"] is True
    assert seen["url"] == ke_matcher.BASE_URL + "/health"


def test_judge_wraps_http_error(monkeypatch):
    import urllib.error

    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 503, "down", {},
                                     io.BytesIO(b'{"detail":"no"}'))

    monkeypatch.setattr(ke_matcher.urllib.request, "urlopen", fake_urlopen)
    try:
        ke_matcher.judge("a", "b")
    except RuntimeError as e:
        assert "KE-matcher 503" in str(e)
    else:
        raise AssertionError("expected RuntimeError")
