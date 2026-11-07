import importlib
from fastapi.testclient import TestClient
import pytest
from analyzer import db


class NoModel:
    ready = False
    error = "Test model unavailable"
    name = "test"
    def start(self): pass
    def close(self): pass
    def rss(self): return 0


@pytest.fixture
def client(tmp_path, monkeypatch):
    app = importlib.import_module('analyzer.app')
    monkeypatch.setattr(db, 'DATA', tmp_path)
    monkeypatch.setattr(app, 'DATA', tmp_path)
    monkeypatch.setattr(app, 'model', NoModel())
    with TestClient(app.app) as client:
        yield client


@pytest.mark.parametrize('filename,data,status', [('bad.csv', b'hello', 400),('empty.log',b'',400),('blank.log',b' \n \t\n',400),('binary.txt',b'a\x00b',400),('badutf8.log',b'\xff\xfe',400)])
def test_invalid_upload_is_rejected_and_cleaned(client, filename, data, status):
    response = client.post('/api/jobs', files={'file': (filename, data)})
    assert response.status_code == status
    assert client.get('/api/jobs').json() == []


def test_valid_upload_and_model_unavailable_preserves_file(client):
    response = client.post('/api/jobs', files={'file': ('../../safe.log', b'{"status":200}\n')})
    assert response.status_code == 201
    job = response.json()
    assert job['filename'] == 'safe.log' and job['lines'] == 1
    assert client.post('/api/jobs/'+job['id']+'/start').status_code == 503
    assert client.get('/api/jobs/'+job['id']).json()['status'] == 'uploaded'
    assert client.get('/api/jobs/'+job['id']+'/evidence').json()[0]['line'] == 1


def test_cross_origin_mutation_blocked(client):
    assert client.post('/api/jobs', headers={'Origin':'https://malicious.example'},files={'file':('a.log',b'x')}).status_code == 403


def test_large_upload_rejected_before_processing(client):
    assert client.post('/api/jobs',content=b'x',headers={'Content-Length':str(9*1024**2)}).status_code == 413


def test_unknown_job_and_invalid_evidence_range(client):
    assert client.get('/api/jobs/missing').status_code == 404
    j=client.post('/api/jobs',files={'file':('a.log',b'hello')}).json()
    assert client.get('/api/jobs/'+j['id']+'/evidence?limit=10000').status_code == 400


def test_offline_interface_assets_have_browser_compatible_types(client):
    page=client.get('/')
    assert page.status_code==200 and 'See the whole story.' in page.text
    for path,kinds in [('/static/fonts/open-sans-variable.ttf',{'font/ttf'}),('/static/theme.css',{'text/css'}),('/static/vendor/silk-waves.js',{'text/javascript','application/javascript'})]:
        result=client.get(path)
        assert result.status_code==200
        assert result.headers['content-type'].split(';')[0] in kinds
        assert len(result.content)>1000
