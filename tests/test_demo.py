from decimal import Decimal

from fastapi.testclient import TestClient

from dolos.api import create_app
from dolos.demo import case_rows, load_dataset
from dolos.migrate import migrate
from dolos.store import Store
from dolos.worker import process_one


def test_dataset_has_citations_and_no_made_up_transaction_fields():
    dataset = load_dataset()
    assert len(dataset['cases']) == 6
    assert sum(len(c['nodes']) for c in dataset['cases']) == 33
    assert sum(len(c['edges']) for c in dataset['cases']) == 29
    sources = {s['id']: s for s in dataset['sources']}
    assert all(s['url'].startswith('https://') for s in sources.values())
    for case in dataset['cases']:
        for row in case_rows(dataset, case):
            assert row['citations'] and row['evidence_status']
            assert row['sender']['account'] is None and row['receiver']['account'] is None
            assert row['timestamp'] is None or row['timestamp_precision'] == 'date'
            assert row['extraction']['provider'] == 'curated_public_sources'
            assert row['evidence']['quote'] is None  # Paraphrases are not represented as quotations.


def test_allegations_aggregates_and_context_are_preserved():
    dataset = load_dataset()
    cases = {c['id']: c for c in dataset['cases']}
    assert all(e['amount'] is None for e in cases['al-capone']['edges'])
    assert all(e['evidence_status'] == 'alleged_in_civil_complaint' for e in cases['1mdb']['edges'])
    wires = [e for e in cases['1mdb']['edges'] if e['relation'] == 'transfer']
    assert {e['amount'] for e in wires} == {'700000000', '1173104', '9000000'}
    assert all(e['date_qualifier'] == 'on_or_about' for e in wires)
    assert all(e['amount'] is None for e in cases['hsbc']['edges'])
    assert cases['hsbc']['metrics'][0]['qualifier'] == 'at_least'
    assert cases['liberty-reserve']['metrics'][0]['scope'] == 'platform_volume_not_all_proven_illicit'
    assert cases['liberty-reserve']['metrics'][1]['qualifier'] == 'range'
    assert next(e for e in cases['bitfinex']['edges'] if e['amount'])['amount_scope'] == 'aggregate_theft'


def test_demo_seed_is_idempotent_and_api_keeps_sources(tmp_path, monkeypatch):
    monkeypatch.delenv('DOLOS_API_KEY', raising=False)
    result = migrate(tmp_path)
    assert result['applied'] == [1, 2, 3]
    assert migrate(tmp_path)['applied'] == []
    store = Store(tmp_path)
    assert len(store.graph_rows()) == 29
    assert store.transactions() == []  # Historical relations never become model-scoring rows.
    assert not process_one(store)  # No fake statement jobs were queued.
    with TestClient(create_app(tmp_path)) as client:
        graph = client.get('/graph').json()
        assert len(graph['nodes']) == 33 and len(graph['edges']) == 29
        assert graph['summaries'] == [] and graph['relation_summaries'] == []
        for edge in graph['edges']:
            assert edge['citations'][0]['url'].startswith('https://')
            assert edge['dataset_id'] == 'famous-cases-v1'
        batch_id = next(row['document_id'].removesuffix('-source-graph') for row in store.relations() if row['case_id'] == '1mdb')
        filtered = client.get('/graph', params={'batch_id': batch_id}).json()
        assert len(filtered['edges']) == 5
        assert all(e['case_id'] == '1mdb' for e in filtered['edges'])
        assert client.get('/demo/cases').json()['cases'][0]['metrics'] == []
        detail = client.get('/demo/cases/1mdb').json()
        assert len(detail['sources']) == 1 and len(detail['edges']) == 5
        assert client.get('/demo/cases/nonexistent').status_code == 404


def test_migrated_cases_and_mixed_transaction_graph(tmp_path, monkeypatch):
    import json
    from dolos.graph import build_graph
    from dolos.parsers import parse_document
    from pathlib import Path
    migrate(tmp_path)
    store = Store(tmp_path)
    assert len(store.graph_rows()) == 29
    _, row, _ = next(parse_document(Path('examples/statement.csv'), '.csv'))
    canonical = dict(row, id='canonical', document_id='uploaded', source_row=1)
    graph = build_graph(store.graph_rows() + [canonical])
    assert len(graph['edges']) == 30
    assert graph['summaries'][0]['amount_paid'] == '125.50'
    assert graph['relation_summaries'] == []
