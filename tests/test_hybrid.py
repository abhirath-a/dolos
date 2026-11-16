"""Actual single-pipeline inference and per-finding attribution checks."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from dolos.api import create_app
from dolos.inference import ModelUnavailable, score
from dolos.model_demo import analyze_demo, demo_paths
from dolos.parsers import parse_document


def test_hybrid_runs_for_every_example_and_credits_sum_to_final_decision():
    data, _, _ = demo_paths()
    assert data['hybrid_run_id'].endswith('-hybrid-v1')
    for example in data['examples']:
        result = analyze_demo('hybrid', example['id'])
        assert result['name'] == 'hybrid'
        assert result['run_id'] == data['hybrid_run_id']
        assert {item['transaction_id'] for item in result['scores']} == set(example['focus_edge_ids'])
        for item in result['scores']:
            explanation = item['explanation']
            if explanation is None:
                continue
            assert explanation['kind'] == 'hybrid'
            credits = explanation['component_contributions']
            assert {credit['name'] for credit in credits} == {'GraphSAGE neighborhood', 'Transaction fields'}
            assert explanation['base_margin'] + sum(credit['contribution'] for credit in credits) == pytest.approx(item['score'], abs=1e-4)
            assert explanation['tree_paths'] and explanation['tree_count'] == 150
            for context in explanation['context_transactions']:
                assert datetime.fromisoformat(context['timestamp']).timestamp() * 1_000_000 < item['context_cutoff_us']


def test_default_api_is_one_hybrid_pipeline(tmp_path, monkeypatch):
    monkeypatch.delenv('DOLOS_API_KEY', raising=False)
    client = TestClient(create_app(tmp_path))
    response = client.post('/demo/model/analyze', params={'example': 'mixed-network'})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['name'] == 'hybrid'
    assert len(result['scores']) == 12
    assert sum(item['score'] > 0 for item in result['scores']) == 4


def test_hybrid_rejects_missing_and_mismatched_artifacts(tmp_path):
    with pytest.raises(ModelUnavailable):
        score([], None, 'hybrid', 1)
    _, directory, source = demo_paths()
    import shutil
    for path in directory.iterdir():
        if path.is_file():
            shutil.copyfile(path, tmp_path / path.name)
    (tmp_path / 'graphsage.npz').write_bytes(b'wrong encoder')
    rows = [dict(row, id=str(index)) for index, row, error in parse_document(source, '.csv') if not error]
    with pytest.raises(ModelUnavailable, match='checksum'):
        score(rows, tmp_path, 'hybrid', 1)


def test_uploaded_hybrid_findings_follow_final_model_not_heuristic_flags(tmp_path, monkeypatch):
    from dolos.worker import process_one
    import dolos.api as api_module
    monkeypatch.setenv('DOLOS_MODEL_DIR', str(demo_paths()[1]))
    app = create_app(tmp_path)
    client = TestClient(app)
    fields = 'timestamp,from_bank,from_account,to_bank,to_account,amount_received,receiving_currency,amount_paid,payment_currency,payment_format\n'
    rows = '2026-01-01T01:00:00Z,bank,A,bank,B,100,USD,100,USD,ACH\n2026-01-01T02:00:00Z,bank,B,bank,C,95,USD,95,USD,ACH\n'
    batch = client.post('/batches', files=[('files', ('test.csv', (fields + rows).encode()))]).json()
    assert process_one(app.state.store)

    def negative_hybrid(history, directory, model, limit, target_ids, explain):
        assert model == 'hybrid'
        return {'scores': [{'transaction_id': identifier, 'score': -1} for identifier in target_ids]}

    monkeypatch.setattr(api_module, 'score', negative_hybrid)
    result = client.post('/analyze', json={'batch_ids': [batch['id']]}).json()
    assert result['model']['name'] == 'hybrid'
    assert result['method'] == 'hybrid_model'
    assert result['findings'] == []
    assert result['pattern_observations']
