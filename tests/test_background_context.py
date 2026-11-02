"""后台任务的 library 上下文：定时体检必须逐库执行，不能落到默认库。"""
from app.library_context import current_library_id, library_session


def test_scheduled_health_uses_each_library(client, monkeypatch):
    from app import healthcheck, main
    from app.db import SessionLocal
    from app.models import OntologyLibrary
    from sqlalchemy import select

    with library_session(None) as db:
        expected = [x.id for x in db.scalars(select(OntologyLibrary))]
    seen = []

    def fake_run_health(db, trigger, *, library_id=None):
        seen.append(library_id or current_library_id.get())
        return None

    monkeypatch.setattr(healthcheck, "run_health", fake_run_health)
    main._scheduled_health()
    assert seen
    assert set(seen) == set(expected)


def test_library_session_scopes_and_closes():
    with library_session(4) as db:
        assert current_library_id.get() == 4
        assert db is not None
    assert current_library_id.get() != 4
