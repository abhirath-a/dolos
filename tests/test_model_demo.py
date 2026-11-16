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


def test_example_query_routes_to_selected_live_targets(tmp_path, monkeypatch):
    monkeypatch.delenv('DOLOS_DEMO_MODEL_DIR', raising=False)
    monkeypatch.delenv('DOLOS_API_KEY', raising=False)
    client = TestClient(create_app(tmp_path / 'uploads'))
    data = json.loads((ROOT / 'frontend/public/model-demo.json').read_text())
    for example in data['examples']:
        for model in ('graphsage', 'graph_xgboost'):
            response = client.post('/demo/model/analyze', params={'model': model, 'example': example['id']})
            assert response.status_code == 200, response.text
            assert {item['transaction_id'] for item in response.json()['scores']} == set(example['focus_edge_ids'])
    assert client.post('/demo/model/analyze', params={'example': 'not-a-demo'}).status_code == 404


def test_packaged_demo_matches_frontend_and_live_predictions(tmp_path, monkeypatch):
    import pytest
    monkeypatch.delenv('DOLOS_DEMO_MODEL_DIR', raising=False)
    monkeypatch.delenv('DOLOS_DEMO_SOURCE_CSV', raising=False)
    monkeypatch.delenv('DOLOS_API_KEY', raising=False)
    from dolos.model_demo import demo_paths
    data, directory, source = demo_paths()
    assert directory.is_relative_to(ROOT / 'dolos/demo_assets')
    assert hashlib.sha256(source.read_bytes()).hexdigest() == data['source_sha256']
    assert data == json.loads((ROOT / 'frontend/public/model-demo.json').read_text())
    client = TestClient(create_app(tmp_path / 'uploads'))
    assert client.get('/demo/model/status').json()['live_available']
    for name in ('graphsage', 'graph_xgboost'):
        response = client.post('/demo/model/analyze', params={'model': name})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['name'] == name
        assert result['scores'][0]['score'] == pytest.approx(data['models'][name]['scores'][0]['score'], abs=1e-4)
        assert result['scores'][0]['explanation']


def test_live_scores_do_not_import_training_stack_and_recalculate_weights(tmp_path, monkeypatch):
    import builtins
    import shutil
    import numpy as np
    import pytest
    from dolos.model_demo import analyze_demo, demo_paths
    monkeypatch.delenv('DOLOS_DEMO_MODEL_DIR', raising=False)
    _, artifacts, _ = demo_paths()
    original_import = builtins.__import__

    def serving_only(name, *args, **kwargs):
        if name.split('.')[0] in {'torch', 'torch_geometric', 'polars', 'sklearn'}:
            raise ImportError('Training dependency blocked by serving-only test')
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', serving_only)
    for name in ('graphsage', 'graph_xgboost'):
        assert analyze_demo(name)['scores'][0]['explanation']
    for filename in ('manifest.json', 'graph_xgboost.json', 'hybrid_xgboost.json', 'hybrid_manifest.json'):
        shutil.copyfile(artifacts / filename, tmp_path / filename)
    with np.load(artifacts / 'graphsage.npz', allow_pickle=False) as original:
        weights = {name: original[name] for name in original.files}
    # A copied test artifact changes only the final head. Fresh inference must
    # evaluate it, rather than returning the recorded fixture's original score.
    weights['head.3.weight'] = np.zeros_like(weights['head.3.weight'])
    weights['head.3.bias'] = np.full_like(weights['head.3.bias'], 1.25)
    np.savez_compressed(tmp_path / 'graphsage.npz', **weights)
    monkeypatch.setenv('DOLOS_DEMO_MODEL_DIR', str(tmp_path))
    assert analyze_demo('graphsage')['scores'][0]['score'] == pytest.approx(1.25)


def test_judge_examples_have_eligible_targets_and_fresh_model_runs(monkeypatch):
    import pytest
    from dolos import inference
    from dolos.model_demo import analyze_demo, demo_paths
    monkeypatch.delenv('DOLOS_DEMO_MODEL_DIR', raising=False)
    monkeypatch.delenv('DOLOS_DEMO_SOURCE_CSV', raising=False)
    data, _, _ = demo_paths()
    examples = data['examples']
    assert {item['id'] for item in examples} == {
        'pass-through', 'incoming-collection', 'ordinary-payments', 'mixed-network'}
    calls = []
    actual_score = inference.score

    def observed_score(*args, **kwargs):
        calls.append((args[2], frozenset(kwargs['target_ids'])))
        return actual_score(*args, **kwargs)

    monkeypatch.setattr(inference, 'score', observed_score)
    for example in examples:
        assert 'models' not in example and 'scores' not in example
        targets = set(example['focus_edge_ids'])
        assert example['focus_edge_id'] in targets
        graph_edges = {edge['id'] for edge in example['graph']['edges']}
        assert targets <= graph_edges
        if example['id'] != 'pass-through':
            assert graph_edges == targets  # History is added only for a pinned prediction.
        for model in ('graphsage', 'graph_xgboost'):
            before = len(calls)
            result = analyze_demo(model, example['id'])
            assert len(calls) == before + 1
            assert calls[-1] == (model, frozenset(targets))
            assert result['status'] == 'completed'
            assert {row['transaction_id'] for row in result['scores']} == targets
            assert result['run_id'] == data['run_id']
            assert any(row['explanation'] for row in result['scores'])
            if model == 'graphsage':
                for row in result['scores']:
                    for context in (row['explanation'] or {}).get('context_transactions', []):
                        assert datetime.fromisoformat(context['timestamp']).timestamp() * 1_000_000 < row['context_cutoff_us']
    analyze_demo('graphsage', 'mixed-network')
    assert len(calls) == 9  # The repeated request executes another real score call.
    with pytest.raises(ValueError, match='Unknown model example'):
        analyze_demo('graphsage', 'not-an-example')
