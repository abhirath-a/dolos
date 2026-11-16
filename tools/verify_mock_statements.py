"""Exercise the video PDFs through upload, worker, graph and both trained models.

Run: uv run --extra ml --extra test python tools/verify_mock_statements.py
Uses an isolated local database/storage; never connects to deployed resources.
"""
import json
import os
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def main():
    pack = ROOT / 'output' / 'pdf'
    manifest = json.loads((pack / 'manifest.json').read_text())
    with tempfile.TemporaryDirectory() as scratch:
        os.environ['DOLOS_DATABASE_URL'] = 'file:' + str(Path(scratch) / 'test.sqlite3')
        os.environ['DOLOS_DATA_DIR'] = str(Path(scratch) / 'uploads')
        os.environ['DOLOS_STORAGE_BACKEND'] = 'local'
        os.environ['DOLOS_MODEL_DIR'] = str(ROOT / 'dolos' / 'demo_assets' / 'artifacts')
        os.environ.pop('DOLOS_API_KEY', None)
        from dolos.api import create_app
        from dolos.worker import process_one
        app = create_app()
        with TestClient(app) as client:
            response = client.post('/batches', files=[('files', (item['pdf'], (pack / item['pdf']).read_bytes(), 'application/pdf')) for item in manifest['files']])
            assert response.status_code == 202, response.text
            batch_id = response.json()['id']
            while process_one(app.state.store):
                pass
            batch = client.get('/batches/' + batch_id).json()
            assert batch['status'] == 'completed', batch
            assert sum(item['accepted'] for item in batch['documents']) == manifest['rows']
            graph = client.get('/graph', params={'batch_id': batch_id, 'limit': 2000}).json()
            assert len(graph['edges']) == manifest['rows']
            assert all(edge['evidence']['attachment'] == 'dolos-transactions.csv' for edge in graph['edges'])
            # A readable final-hour graph; earlier imported transfers remain model history.
            latest = max(datetime.fromisoformat(edge['timestamp']) for edge in graph['edges'])
            after = latest.replace(minute=0, second=0, microsecond=0).isoformat()
            visible = client.get('/graph', params={'batch_id': batch_id, 'after': after, 'limit': 2000}).json()
            report = {'fixture': 'synthetic teaching data, not a real-world accuracy benchmark',
                      'documents': len(batch['documents']), 'accepted': manifest['rows'],
                      'graph_nodes': len(graph['nodes']), 'graph_edges': len(graph['edges']),
                      'video_window_after_utc': after, 'visible_edges': len(visible['edges']), 'models': {}}
            for model in ('graphsage', 'graph_xgboost'):
                full = client.post('/analyze', json={'batch_ids': [batch_id], 'model': model})
                assert full.status_code == 200, full.text
                full_prediction = full.json()['model']
                assert full_prediction['status'] == 'completed', full_prediction
                scored_documents = {item['transaction_id'].rsplit(':', 1)[0] for item in full_prediction['scores']}
                assert scored_documents == {item['id'] for item in batch['documents']}, 'Each statement must include eligible targets'
                response = client.post('/analyze', json={'batch_ids': [batch_id], 'edge_ids': [edge['id'] for edge in visible['edges']], 'model': model})
                assert response.status_code == 200, response.text
                result = response.json()
                prediction = result['model']
                assert prediction['status'] == 'completed', prediction
                assert len(prediction['scores']) == len(visible['edges']), prediction
                assert any(item.get('explanation') for item in prediction['scores'])
                # IDs contain random document IDs; record reproducible source details instead.
                by_id = {edge['id']: edge for edge in visible['edges']}
                report['models'][model] = {'status': prediction['status'], 'run_id': prediction['run_id'],
                    'full_batch_scored': len(full_prediction['scores']), 'all_documents_have_eligible_targets': True,
                    'history_rows': prediction['history_rows'], 'scored': len(prediction['scores']),
                    'positive_decisions': sum(item['score'] > 0 for item in prediction['scores']),
                    'scores': [{'timestamp': by_id[item['transaction_id']]['timestamp'],
                                'source': by_id[item['transaction_id']]['source'],
                                'target': by_id[item['transaction_id']]['target'],
                                'raw_margin': item['score'],
                                'explanation': (item.get('explanation') or {}).get('kind')}
                               for item in prediction['scores']]}
            (pack / 'validation.json').write_text(json.dumps(report, indent=2) + '\n')
            with ZipFile(pack / 'dolos-video-statements.zip', 'w', ZIP_DEFLATED) as archive:
                for path in sorted(pack.iterdir()):
                    if path.suffix in {'.pdf', '.csv', '.json', '.md'}:
                        archive.write(path, path.name)
            (ROOT / 'frontend/public/dolos-video-statements.zip').write_bytes((pack / 'dolos-video-statements.zip').read_bytes())
            print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
