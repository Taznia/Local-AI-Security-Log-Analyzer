import json
import uuid
import httpx
import pytest
from analyzer import db
from analyzer.llm import LocalModel, ModelError
from analyzer.pipeline import Engine, grounded_schema
from analyzer.prompts import MAP_SCHEMA
from tests.test_safety import add_job, auth, BudgetModel


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    db.initialize()
    return tmp_path


def test_restart_marks_unfinished_job_interrupted(workspace):
    job = add_job(workspace, ['entry'])
    db.update_job(job, status='running', completed=2, total=5)
    db.initialize()
    saved = db.get_job(job)
    assert saved['status'] == 'interrupted' and saved['completed'] == 2


def test_grammar_limits_line_numbers_and_does_not_mutate_global_schema():
    schema = grounded_schema(MAP_SCHEMA, {7, 11}, True)
    assert schema['properties']['signals']['items']['properties']['lines']['items']['enum'] == [7, 11]
    assert 'enum' not in MAP_SCHEMA['properties']['signals']['items']['properties']['lines']['items']


def test_retry_persists_attempts_and_rejects_truncated_json(workspace, monkeypatch):
    job = add_job(workspace, ['entry'])
    model = LocalModel()
    model.ready = True
    model.template = lambda system, user: 'prompt'
    model.tokens = lambda text: [1, 2, 3]
    model.count = lambda text: 3
    responses = iter(['{"signals":[', '{"signals":[]}'])
    def transport(request):
        return httpx.Response(200, json={'content': next(responses), 'tokens_predicted': 5, 'truncated': False})
    model.client.close()
    model.client = httpx.Client(transport=httpx.MockTransport(transport), base_url='http://127.0.0.1:8091')
    monkeypatch.setattr('analyzer.llm.time.sleep', lambda seconds: None)
    result, meta = model.call(job, 'map', 0, 's', 'u', MAP_SCHEMA, 384, lambda value: None)
    assert result == {'signals': []} and meta['attempts'] == 2
    with db.connect() as c:
        attempts = list(c.execute('SELECT status,error FROM calls WHERE job_id=? ORDER BY id', (job,)))
    assert [r['status'] for r in attempts] == ['failed', 'ok']
    assert 'incomplete' in attempts[0]['error']
    model.close()


def test_exhausted_retries_keep_existing_chunk(workspace, monkeypatch):
    job = add_job(workspace, ['entry'])
    with db.connect() as c:
        c.execute("INSERT INTO chunks(job_id,idx,content,lines,status,result) VALUES(?,?,?,?,?,?)", (job, 0, 'L1: entry', '[1]', 'done', '{"signals":[]}'))
    model = LocalModel(); model.ready = True
    model.template = lambda s, u: 'prompt'; model.tokens = lambda t: [1]; model.count = lambda t: 1
    model.client.close()
    model.client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503, text='busy')),base_url='http://127.0.0.1:8091')
    monkeypatch.setattr('analyzer.llm.time.sleep', lambda seconds: None)
    with pytest.raises(ModelError, match='3 attempts'):
        model.call(job, 'map', 1, 's', 'u', MAP_SCHEMA, 384, lambda v: None)
    with db.connect() as c:
        assert c.execute("SELECT status FROM chunks WHERE job_id=? AND idx=0", (job,)).fetchone()[0] == 'done'
        assert c.execute('SELECT COUNT(*) FROM calls WHERE job_id=?', (job,)).fetchone()[0] == 3
    model.close()


def test_timeout_waits_for_idle_before_safe_retry(workspace, monkeypatch):
    job = add_job(workspace, ['entry'])
    model = LocalModel(); model.ready = True
    model.template = lambda s,u: 'prompt'; model.tokens = lambda t: [1]; model.count = lambda t: 1
    events = []
    def transport(request):
        if not events:
            events.append('timeout')
            raise httpx.ReadTimeout('simulated model timeout', request=request)
        assert events == ['timeout','idle']
        events.append('retry')
        return httpx.Response(200,json={'content':'{"signals":[]}', 'tokens_predicted':5})
    model.client.close()
    model.client=httpx.Client(transport=httpx.MockTransport(transport),base_url='http://127.0.0.1:8091')
    model.wait_idle=lambda:events.append('idle')
    monkeypatch.setattr('analyzer.llm.time.sleep',lambda seconds:None)
    result,meta=model.call(job,'map',0,'s','u',MAP_SCHEMA,384,lambda v:None)
    assert events == ['timeout','idle','retry'] and meta['attempts']==2
    assert result == {'signals':[]}
    with db.connect() as c:
        assert [r[0] for r in c.execute('SELECT status FROM calls WHERE job_id=? ORDER BY id',(job,))] == ['failed','ok']
    model.close()


class CheckpointModel(BudgetModel):
    ready = True
    error = None
    name = 'checkpoint-test'
    fingerprint = 'checkpoint-model'
    peak_rss = 0
    def __init__(self): self.calls = []
    def rss(self): return 100
    def call(self, job, stage, unit, system, user, schema, output, validate):
        self.calls.append((stage, unit))
        result = {'signals': []} if stage == 'map' else {'report': False, 'severity': 'Low', 'reason': 'Insufficient evidence', 'action': 'Review', 'lines': []}
        validate(result)
        return result, {'prompt_tokens': 100, 'output_tokens': 10, 'duration': 0.1, 'attempts': 1}


def test_resume_skips_completed_calls(workspace):
    job = add_job(workspace, ['ordinary entry', 'ordinary entry two'])
    model = CheckpointModel()
    with db.connect() as c:
        c.execute("INSERT INTO chunks(job_id,idx,content,lines,status,result) VALUES(?,?,?,?,?,?)", (job, 0, 'L1: ordinary entry', '[1]', 'done', '{"signals":[]}'))
        c.execute("INSERT INTO chunks(job_id,idx,content,lines) VALUES(?,?,?,?)", (job, 1, 'L2: ordinary entry two', '[2]'))
    db.update_job(job, total=2, completed=1, status='interrupted')
    engine = Engine(model)
    try:
        engine.analyze(job)
        assert model.calls == [('map', 1)]
        assert db.get_job(job)['status'] == 'completed'
        assert db.get_job(job)['completed'] == 2
    finally:
        engine.close()
