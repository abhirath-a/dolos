from fastapi.testclient import TestClient
from dolos.api import create_app
from dolos.analysis import analyze_graph


def edge(id, source, target, time, amount='100', currency='USD'):
    return dict(id=id, source=source, target=target, timestamp=time, amount=amount, currency=currency)


def test_rules_require_comparable_currency_amount_and_time():
    first = edge('in', 'a', 'b', '2026-01-01T01:00:00+00:00')
    second = edge('out', 'b', 'c', '2026-01-01T02:00:00+00:00', '95')
    result = analyze_graph({'edges': [first, second]})
    assert {item['edge_id'] for item in result['findings']} == {'in', 'out'}
    assert result['findings'][0]['reasons'][0]['related_edges'] == ['in', 'out']
    for changes in ({'currency': 'EUR'}, {'amount': None}, {'timestamp': None},
                    {'timestamp': '2026-01-03T02:00:00+00:00'}, {'amount': '1'}):
        assert not analyze_graph({'edges': [first, dict(second, **changes)]})['findings']


def test_reciprocal_rules_do_not_flag_self_transfers():
    assert not analyze_graph({'edges': [edge('s', 'a', 'a', None)]})['findings']
    result = analyze_graph({'edges': [edge('a', 'x', 'y', None), edge('b', 'y', 'x', None)]})
    assert len(result['findings']) == 2
    assert result['findings'][0]['reasons'][0]['rule'] == 'Reciprocal transfers'


def test_historical_analysis_is_cited_and_never_claims_model_ran(tmp_path):
    client = TestClient(create_app(tmp_path))
    response = client.post('/analyze', json={'case_id': '1mdb'})
    assert response.status_code == 200
    result = response.json()
    assert result['method'] == 'historical_evidence'
    assert result['model']['status'] == 'not_applicable'
    assert result['findings']
    case = client.get('/demo/cases/1mdb').json()
    by_id = {edge['id']: edge for edge in case['edges']}
    assert all(by_id[item['edge_id']]['citations'] for item in result['findings'])
    assert client.post('/analyze', json={'case_id': 'bad'}).status_code == 404
    assert client.post('/analyze', json={}).status_code == 422
    assert client.post('/analyze', json={'batch_ids': ['missing']}).status_code == 404
    assert client.post('/analyze', json={'batch_ids': ['x'], 'case_id': '1mdb'}).status_code == 422


def test_analyze_uploaded_batch_and_explicit_model_unavailable(tmp_path, monkeypatch):
    from dolos.worker import process_one
    monkeypatch.delenv('DOLOS_MODEL_DIR', raising=False)
    app = create_app(tmp_path)
    client = TestClient(app)
    fields = 'timestamp,from_bank,from_account,to_bank,to_account,amount_received,receiving_currency,amount_paid,payment_currency,payment_format\n'
    rows = '2026-01-01T01:00:00Z,bank,A,bank,B,100,USD,100,USD,ACH\n2026-01-01T02:00:00Z,bank,B,bank,C,95,USD,95,USD,ACH\n'
    uploaded = client.post('/batches', files=[('files', ('test.csv', (fields+rows).encode(), 'text/csv'))])
    assert uploaded.status_code == 202
    assert process_one(app.state.store)
    result = client.post('/analyze', json={'batch_ids': [uploaded.json()['id']]}).json()
    assert result['model']['status'] == 'unavailable'
    assert len(result['findings']) == 2
    assert result['scope']['model_eligible'] == 2


def test_tree_paths_follow_real_branches():
    import json
    from dolos.inference import tree_paths
    class Booster:
        def get_dump(self, dump_format):
            return [json.dumps({'nodeid':0,'split':'f0','split_condition':2,'yes':1,'no':2,
                'children':[{'nodeid':1,'leaf':-.2},{'nodeid':2,'leaf':.4}]})]
    result = tree_paths(Booster(), [3], ['Amount'])
    assert result[0]['steps'][0]['branch'] == 'greater_or_equal'
    assert result[0]['leaf_margin'] == .4


def test_web_mounts_api_and_frontend(tmp_path):
    from dolos.web import create_web_app
    (tmp_path / 'index.html').write_text('<html><title>Dolos</title></html>')
    client = TestClient(create_web_app(tmp_path))
    assert client.get('/').status_code == 200
    assert client.get('/api/health').json()['status'] == 'ok'
    assert client.get('/api/demo/cases').status_code == 200


def test_model_findings_use_only_requested_batch_edges(tmp_path, monkeypatch):
    from dolos.worker import process_one
    import dolos.api as api_module
    app = create_app(tmp_path)
    client = TestClient(app)
    fields = 'timestamp,from_bank,from_account,to_bank,to_account,amount_received,receiving_currency,amount_paid,payment_currency,payment_format\n'
    rows = '2026-01-01T01:00:00Z,bank,A,bank,B,100,USD,100,USD,ACH\n2026-02-01T02:00:00Z,bank,B,bank,C,95,USD,95,USD,ACH\n'
    batch = client.post('/batches', files=[('files', ('model.csv', (fields+rows).encode(), 'text/csv'))]).json()
    assert process_one(app.state.store)
    graph = client.get('/graph', params={'batch_id': batch['id']}).json()
    chosen = graph['edges'][0]
    def fake_score(history, directory, model, limit, target_ids, explain):
        assert target_ids == {chosen['id']}
        assert explain
        return {'scores': [{'transaction_id': chosen['id'], 'score': 1.3,
                            'explanation': {'kind': 'tree_shap', 'features': []}}], 'run_id': 'test-artifact'}
    monkeypatch.setattr(api_module, 'score', fake_score)
    result = client.post('/analyze', json={'batch_ids': [batch['id']], 'edge_ids': [chosen['id']]}).json()
    assert result['scope']['relationships'] == 1
    assert result['model']['status'] == 'completed'
    assert [finding['edge_id'] for finding in result['findings']] == [chosen['id']]
    assert result['entities'][0]['flagged_connections'] == 1


def test_graph_date_slice_is_inclusive_after_exclusive_before(tmp_path):
    from dolos.worker import process_one
    app=create_app(tmp_path)
    client=TestClient(app)
    fields='timestamp,from_bank,from_account,to_bank,to_account,amount_received,receiving_currency,amount_paid,payment_currency,payment_format\n'
    rows=''.join(f'2026-01-0{day}T01:00:00Z,bank,A,bank,B,100,USD,100,USD,ACH\n' for day in (1,2,3))
    batch=client.post('/batches',files=[('files',('dates.csv',(fields+rows).encode(),'text/csv'))]).json()
    assert process_one(app.state.store)
    response=client.get('/graph',params={'batch_id':batch['id'],'after':'2026-01-02T01:00:00Z','before':'2026-01-03T01:00:00Z'})
    assert response.status_code==200
    assert len(response.json()['edges'])==1
    assert '2026-01-02' in response.json()['edges'][0]['timestamp']
    assert client.get('/graph',params={'after':'bad'}).status_code==422
    assert client.get('/graph',params={'after':'2026-01-03T00:00:00Z','before':'2026-01-02T00:00:00Z'}).status_code==422
