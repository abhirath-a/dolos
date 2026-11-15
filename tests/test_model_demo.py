import hashlib
import json
from pathlib import Path
from datetime import datetime
from fastapi.testclient import TestClient
from dolos.api import create_app

ROOT=Path(__file__).parents[1]


def test_recorded_model_demo_has_matching_source_and_true_context():
    data=json.loads((ROOT/'frontend/public/model-demo.json').read_text())
    assert data['provenance']['kind']=='synthetic'
    assert data['execution_label']=='Recorded model run'
    assert hashlib.sha256((ROOT/'frontend/public/demo-transfers.csv').read_bytes()).hexdigest()==data['source_sha256']
    assert data['source_rows']==2880
    graph_edges={edge['id'] for edge in data['graph']['edges']}
    for model in ('graphsage','graph_xgboost'):
        assert data['models'][model]['run_id']==data['run_id']
        result=data['models'][model]['scores'][0]
        assert result['transaction_id']==data['focus_edge_id']
        assert result['transaction_id'] in graph_edges
        detail=result['explanation']
        if model=='graphsage':
            assert detail['context_transactions']
            assert set(detail['context_transaction_ids']) <= graph_edges
            for row in detail['context_transactions']:
                assert datetime.fromisoformat(row['timestamp']).timestamp()*1_000_000<result['context_cutoff_us']
                assert row['hop'] in (1,2)
        else:
            assert abs(detail['base_margin']+detail['contribution_sum']-result['score'])<1e-4
            assert detail['tree_paths']


def test_demo_live_status_is_explicit_when_artifacts_missing(tmp_path,monkeypatch):
    monkeypatch.setenv('DOLOS_DEMO_MODEL_DIR',str(tmp_path/'missing'))
    client=TestClient(create_app(tmp_path/'uploads'))
    assert client.get('/demo/model/status').json()['live_available'] is False
    assert client.post('/demo/model/analyze?model=graphsage').status_code==503
    assert client.post('/demo/model/analyze?model=bad').status_code==422
