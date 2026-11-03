"""DataMaker 页面与任务 API（不启动真实子进程）。"""
from conftest import login


def _ensure_archer(client):
    login(client, "admin")
    r = client.post("/api/libraries", json={
        "key": "archer", "name": "Archer Benchmark", "kind": "reference"})
    assert r.status_code in (201, 409), r.text
    r = client.get("/library/switch", params={"key": "archer", "next": "/datamaker"},
                   follow_redirects=False)
    assert r.status_code == 303


def test_datamaker_page(client):
    _ensure_archer(client)
    r = client.get("/datamaker")
    assert r.status_code == 200, r.text
    assert "数据生成" in r.text
    assert "Archer Benchmark" in r.text


def test_datamaker_start_and_job_page(client, monkeypatch, tmp_path):
    _ensure_archer(client)
    from app.models import DatamakerJob
    from app.routers import datamaker as router
    created = {}

    def fake_start(db, *, library, db_id, model, n, workers, ke_types, resume, actor, **kw):
        job = DatamakerJob(
            library_id=library.id, db_id=db_id, status="queued", model=model,
            n=n, workers=workers, ke_types=ke_types, params={"resume": resume},
            workdir=str(tmp_path), log_path=str(tmp_path / "job.log"),
            created_by=actor,
        )
        (tmp_path / "ke1.jsonl").write_text(
            '{"question":"How many?","ke":"output = Count({x | x: T});","type":"aggregate"}\n',
            encoding="utf-8")
        (tmp_path / "job.log").write_text("queued\n", encoding="utf-8")
        db.add(job)
        db.commit()
        db.refresh(job)
        created["job"] = job
        return job

    monkeypatch.setattr(router.service, "start_job", fake_start)
    monkeypatch.setattr(router.service, "recover_stale", lambda db: None)
    r = client.post("/api/datamaker/jobs", json={
        "db_id": "concert_singer", "model": "qwen3-coder-flash",
        "n": 2, "workers": 1, "ke_types": "aggregate", "resume": False})
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["status"] == "queued"
    assert job["db_id"] == "concert_singer"

    r = client.get(f"/datamaker/jobs/{job['id']}")
    assert r.status_code == 200, r.text
    assert "How many?" in r.text
    assert "运行日志" in r.text

    r = client.get("/api/datamaker/jobs")
    assert r.status_code == 200
    assert any(x["id"] == job["id"] for x in r.json()["items"])

    r = client.post(f"/api/datamaker/jobs/{job['id']}/cancel")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"

    assert client.get(f"/api/datamaker/jobs/{job['id']}/artifacts/../x").status_code == 404
    r = client.post("/api/datamaker/jobs", json={
        "db_id": "concert_singer", "model": "qwen3-coder-flash",
        "n": 1, "workers": 1, "service_url": "http://evil.example"})
    assert r.status_code == 400


def test_datamaker_allows_other_library(client):
    login(client, "admin")
    client.get("/library/switch", params={"key": "co-baseline", "next": "/datamaker"},
               follow_redirects=False)
    r = client.get("/datamaker")
    assert r.status_code == 200
    assert "Ontology-only" in r.text
    assert "co-baseline" in r.text


def test_datamaker_records_and_artifact_guard(tmp_path):
    from types import SimpleNamespace
    from app import datamaker

    (tmp_path / "ke1.jsonl").write_text(
        '{"question":"q1","ke":"output=1;"}\nnot-json\n{"question":"q2","ke":"output=2;"}\n',
        encoding="utf-8")
    (tmp_path / "job.log").write_text("hello", encoding="utf-8")
    job = SimpleNamespace(workdir=str(tmp_path), log_path=str(tmp_path / "job.log"))
    rows = datamaker._records(tmp_path / "ke1.jsonl")
    assert [r["question"] for r in rows] == ["q1", "q2"]
    assert datamaker.preview(job, 1)[0]["question"] == "q1"
    assert datamaker.artifact_path(job, "job.log").name == "job.log"
    assert datamaker.artifact_path(job, "../ke1.jsonl") is None
    assert {a["name"] for a in datamaker.artifacts(job)} == {"ke1.jsonl", "job.log"}


def test_import_results_creates_draft_batch(tmp_path):
    from app.db import SessionLocal
    from app.library_context import library_scope
    from app.models import AnnotationBatch, AnnotationUnit, DatamakerJob, OntologyLibrary
    from app import datamaker

    with library_scope(None):
        db = SessionLocal()
        lib = db.scalar(__import__('sqlalchemy').select(OntologyLibrary)
                        .where(OntologyLibrary.key == 'dm-test'))
        if lib is None:
            lib = OntologyLibrary(key='dm-test', name='DM Test', kind='reference')
            db.add(lib)
            db.commit()
            db.refresh(lib)
        job = DatamakerJob(library_id=lib.id, db_id='concert_singer', status='succeeded',
                           model='m', n=1, workers=1, workdir=str(tmp_path),
                           created_by='tester')
        db.add(job)
        db.commit()
        db.refresh(job)
        lib_id, job_id = lib.id, job.id
        db.close()

    (tmp_path / 'ke1.jsonl').write_text(
        '{"question":"How many?","ke":"output=1;","type":"aggregate"}\n',
        encoding='utf-8')
    with library_scope(lib_id):
        db = SessionLocal()
        job = db.get(DatamakerJob, job_id)
        datamaker._import_results(db, job)
        assert job.result_count == 1 and job.imported_count == 1
        assert job.annotation_batch_id
        batch = db.get(AnnotationBatch, job.annotation_batch_id)
        assert batch.state == 'draft'
        unit = db.scalar(__import__('sqlalchemy').select(AnnotationUnit)
                         .where(AnnotationUnit.batch_id == batch.id))
        assert unit.raw_text == 'How many?'
        assert unit.source_payload['ke'] == 'output=1;'
        assert job.gold_count == 1
        from app.models import GoldItem
        g = db.scalar(__import__('sqlalchemy').select(GoldItem).where(
            GoldItem.article_code == f'DM-{job.id}'))
        assert g is not None and g.status == 'draft'
        assert g.validation is not None
        db.close()


def test_quality_and_aggregate(tmp_path):
    from types import SimpleNamespace
    from app import datamaker
    (tmp_path / "ke1.jsonl").write_text(
        '{"question":"q","ke":"output=1;","type":"filter","domain":"Concert"}\n',
        encoding="utf-8")
    (tmp_path / "coverage.json").write_text(
        '{"ontology":{"used":3,"total":25},"builtin":{"used":0,"total":29}}',
        encoding="utf-8")
    (tmp_path / "spec.json").write_text(
        '{"n_actual":1,"coverage":{"domain_missing":["Singer"]}}', encoding="utf-8")
    job = SimpleNamespace(workdir=str(tmp_path), n=2, result_count=1, imported_count=1)
    q = datamaker.quality(job)
    assert q["generated"] == 1 and q["completion_rate"] == 50.0
    assert q["ontology_used"] == 3 and q["coverage_missing"] == ["Singer"]
    stats = datamaker.aggregate([SimpleNamespace(status="succeeded", result_count=1,
                                                 imported_count=1),
                                 SimpleNamespace(status="failed", result_count=0,
                                                 imported_count=0)])
    assert stats["success_rate"] == 50.0 and stats["generated"] == 1


def test_retry_api(client, monkeypatch, tmp_path):
    _ensure_archer(client)
    from app.models import DatamakerJob
    from app.routers import datamaker as router
    lib_id = client.get("/api/libraries").json()["items"]
    # Use the current archer library id from the test DB.
    from app.db import SessionLocal
    from app.library_context import library_scope
    with library_scope(None):
        db = SessionLocal()
        from app.models import OntologyLibrary
        from sqlalchemy import select
        lib = db.scalar(select(OntologyLibrary).where(OntologyLibrary.key == "archer"))
        job = DatamakerJob(library_id=lib.id, db_id="concert_singer", status="failed",
                           model="qwen3-coder-flash", n=1, workers=1, workdir=str(tmp_path),
                           created_by="admin")
        db.add(job); db.commit(); db.refresh(job)
        jid = job.id
        db.close()
    new_job = DatamakerJob(library_id=lib.id, db_id="concert_singer", status="queued",
                           model="qwen3-coder-flash", n=1, workers=1,
                           workdir=str(tmp_path), created_by="admin")
    with library_scope(None):
        db = SessionLocal(); db.add(new_job); db.commit(); db.refresh(new_job)
        nid = new_job.id; db.close()
    monkeypatch.setattr(router.service, "retry_job",
                        lambda db, *, library, job, actor: db.get(DatamakerJob, nid))
    r = client.post(f"/api/datamaker/jobs/{jid}/retry")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == nid


def test_command_modes(tmp_path):
    from types import SimpleNamespace
    from app import datamaker

    db = tmp_path / "x.sqlite"
    for mode, token in (("gen-ke", "gen_ke.py"), ("roundtrip", "all"),
                        ("tri", "tri")):
        job = SimpleNamespace(
            workdir=str(tmp_path), db_id="d", n=1, workers=1,
            model="m", ke_types="", params={"mode": mode,
                                            "service_url": "http://127.0.0.1:8081",
                                            "nl_source": "ke", "cross_check": True},
        )
        cmd = datamaker._command(job, db)
        assert any(str(x).endswith(token) for x in cmd)
        if mode != "gen-ke":
            assert "--service-url" in cmd and "--z3-path" in cmd


def test_generic_registry_and_worker_pickup(client):
    from app.db import SessionLocal
    from app.library_context import library_scope
    from app.models import DatamakerJob, OntologyLibrary
    from app import datamaker, datamaker_worker
    with library_scope(None):
        db = SessionLocal()
        lib = db.scalar(__import__('sqlalchemy').select(OntologyLibrary)
                        .where(OntologyLibrary.key == 'co-baseline'))
        assert lib is not None
        with library_scope(lib.id):
            path, reg = datamaker._registry(db, '__ontology__', lib.id)
            assert path is None
            assert reg['concepts'] and reg['operators']
            assert reg.get('_name_map')
        from sqlalchemy import update
        db.execute(update(DatamakerJob).where(DatamakerJob.status == 'queued')
                   .values(status='cancelled'))
        job = DatamakerJob(library_id=lib.id, db_id='__ontology__', status='queued',
                           model='m', n=1, workers=1, created_by='tester')
        db.add(job); db.commit(); db.refresh(job)
        job_id, lib_id = job.id, lib.id
        db.close()
    found = datamaker_worker.service.claim_next_job('test-worker')
    assert found == (job_id, lib_id)


def test_gold_gate_blocks_auto_gold(tmp_path):
    from app.db import SessionLocal
    from app.library_context import library_scope
    from app.models import DatamakerJob, GoldItem, OntologyLibrary
    from app import datamaker
    import json
    import sqlalchemy as sa

    (tmp_path / "ke1.jsonl").write_text(
        '{"question":"q","ke":"output = 1;","type":"scalar"}\n', encoding="utf-8")
    (tmp_path / "gold_eval.json").write_text(json.dumps({
        "total": 1, "execution_accuracy": 0.0,
        "gate": {"passed": False, "execution_rate": 0.0,
                 "execution_accuracy": 0.0, "reason": "below threshold"},
    }), encoding="utf-8")
    with library_scope(None):
        db = SessionLocal()
        lib = db.scalar(sa.select(OntologyLibrary).where(OntologyLibrary.key == "co-baseline"))
        job = DatamakerJob(library_id=lib.id, db_id="__ontology__", status="succeeded",
                           model="m", n=1, workers=1, workdir=str(tmp_path),
                           created_by="tester")
        db.add(job); db.commit(); db.refresh(job)
        lib_id, job_id = lib.id, job.id
        db.close()
    with library_scope(lib_id):
        db = SessionLocal()
        job = db.get(DatamakerJob, job_id)
        datamaker._import_results(db, job)
        assert job.gold_count == 0
        assert job.gold_gate and job.gold_gate["passed"] is False
        assert db.scalar(sa.select(GoldItem).where(GoldItem.article_code == f"DM-{job.id}")) is None
        db.close()


def test_native_job_sync(tmp_path, monkeypatch):
    from app.db import SessionLocal
    from app.library_context import library_scope
    from app.models import DatamakerJob, OntologyLibrary
    from app import datamaker
    import sqlalchemy as sa
    with library_scope(None):
        db = SessionLocal()
        lib = db.scalar(sa.select(OntologyLibrary).where(OntologyLibrary.key == "co-baseline"))
        job = DatamakerJob(library_id=lib.id, db_id="__ontology__", status="running",
                           model="m", n=1, workers=1, workdir=str(tmp_path),
                           native_job_id="native-1", created_by="tester")
        db.add(job); db.commit(); db.refresh(job)
        lib_id, job_id = lib.id, job.id
        db.close()
    monkeypatch.setattr(datamaker, "NATIVE_API_URL", "http://127.0.0.1:8090")
    monkeypatch.setattr(datamaker, "_native_call", lambda *a, **kw: {
        "id": "native-1", "status": "succeeded", "progress": 1, "result_count": 1})
    monkeypatch.setattr(datamaker, "_import_results", lambda db, job: None)
    with library_scope(lib_id):
        db = SessionLocal()
        job = db.get(DatamakerJob, job_id)
        datamaker.sync_native_jobs(db, lib_id)
        assert job.status == "succeeded"
        db.close()


def test_start_native_job_records_external_id(client, monkeypatch):
    from app.db import SessionLocal
    from app.library_context import library_scope
    from app.models import DatamakerJob, OntologyLibrary
    from app import datamaker
    import sqlalchemy as sa
    with library_scope(None):
        db = SessionLocal()
        lib = db.scalar(sa.select(OntologyLibrary).where(OntologyLibrary.key == "co-baseline"))
        lib_id = lib.id
        db.close()
    monkeypatch.setattr(datamaker, "NATIVE_API_URL", "http://127.0.0.1:8093")
    monkeypatch.setattr(datamaker, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(datamaker, "LLM_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setattr(datamaker, "_native_call",
                        lambda method, path, body=None: {"id": "native-test", "status": "queued"})
    monkeypatch.setattr(datamaker, "active_count", lambda db, library_id: 0)
    with library_scope(lib_id):
        db = SessionLocal()
        lib = db.get(OntologyLibrary, lib_id)
        job = datamaker.start_job(db, library=lib, db_id="__ontology__",
                                  model=datamaker.MODELS[0], n=1, workers=1,
                                  ke_types="", resume=False, actor="tester")
        assert job.native_job_id == "native-test"
        assert job.status == "queued"
        assert (job.params or {}).get("native_api") is True
        db.delete(job)
        db.commit()
        db.close()
