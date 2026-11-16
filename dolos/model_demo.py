"""Reproducible, explicitly synthetic teaching demo with actual trained-model outputs.

Run: uv run --extra ml python -m dolos.model_demo
This is not IBM data, a real investigation, or an accuracy benchmark.
"""
import csv
import hashlib
import json
import random
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path


def generate_csv(path):
    from .parsers import IBM_HEADER
    rng = random.Random(20261115)
    start = datetime(2025, 1, 6)
    with path.open('w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(IBM_HEADER)
        for hour in range(240):
            # Repeated, labeled routing motifs alternate with ordinary simulated payments.
            for index in range(12):
                patterned = index < 4
                if patterned:
                    sender, receiver = [(0, 3), (1, 3), (3, 5), (5, 8)][index]
                    amount = rng.choice([8100, 8400, 8700, 9200])
                else:
                    sender = rng.randrange(6, 18)
                    receiver = rng.randrange(6, 18)
                    while receiver == sender:
                        receiver = rng.randrange(6, 18)
                    amount = rng.randrange(50, 9400)
                at = start + timedelta(hours=hour, minutes=index * 4)
                writer.writerow([at.strftime('%Y/%m/%d %H:%M'), 'DEMO', f'{sender:04d}',
                    'DEMO', f'{receiver:04d}', amount, 'USD', amount, 'USD', 'WIRE', int(patterned)])


def build_demo(output=None, epochs=8):
    from .model import run
    from .inference import score
    from .parsers import parse_document
    from .graph import build_graph
    root = Path(__file__).parent.parent
    work = root / 'var' / 'model-demo'
    work.mkdir(parents=True, exist_ok=True)
    source = work / 'synthetic-transfers.csv'
    generate_csv(source)
    fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
    artifacts = work / (fingerprint[:12] + '-e' + str(epochs))
    if not (artifacts / 'manifest.json').exists():
        run(source, artifacts, epochs=epochs, batch_size=192)
    rows = []
    for index, row, error in parse_document(source, '.csv'):
        if error:
            raise ValueError(error)
        rows.append(dict(row, id=f'simulation:{index}', document_id='synthetic-transfers.csv', source_row=index))
    predictions = score(rows, artifacts, 'graphsage', 500)
    # Present the actual highest model margin, including its true sampled history.
    focus = predictions['scores'][0]['transaction_id']
    selected_ids = {focus}
    models = {}
    for model in ('graphsage', 'graph_xgboost'):
        prediction = score(rows, artifacts, model, 1, target_ids={focus}, explain=True)
        prediction['status'] = 'completed'
        prediction['name'] = model
        prediction['threshold'] = 0
        models[model] = prediction
        for item in prediction['scores']:
            selected_ids.update((item.get('explanation') or {}).get('context_transaction_ids', []))
    # Include the source/receiver's recent transfers for a readable local graph.
    focus_row = next(row for row in rows if row['id'] == focus)
    neighbors = [row for row in rows if row['timestamp'] <= focus_row['timestamp'] and
                 (row['sender_id'] in (focus_row['sender_id'], focus_row['receiver_id']) or
                  row['receiver_id'] in (focus_row['sender_id'], focus_row['receiver_id']))]
    selected_ids.update(row['id'] for row in neighbors[-18:])
    display_rows = [row for row in rows if row['id'] in selected_ids]
    graph = build_graph(display_rows)
    for node in graph['nodes']:
        node['label'] = 'Account ' + node['account']
    manifest = json.loads((artifacts / 'manifest.json').read_text())
    payload = {
        'id': 'graphsage-simulation', 'title': 'The pass-through network',
        'description': 'A simulated transaction network with repeated routing patterns. Inspect a real GraphSAGE prediction and its sampled historical neighborhood.',
        'data_label': 'Synthetic teaching data', 'execution_label': 'Recorded model run',
        'notice': 'Generated teaching data and a small demonstration model. These are actual model outputs, not a real investigation or evidence of real-world accuracy.',
        'generated_at': datetime.now(timezone.utc).isoformat(), 'source_sha256': fingerprint,
        'generator': 'dolos/model_demo.py', 'source_rows': len(rows), 'epochs': epochs,
        'run_id': manifest['run_id'], 'artifact_sha256': manifest['artifact_sha256'],
        'focus_edge_id': focus, 'graph': graph, 'models': models,
        'provenance': {'kind': 'synthetic', 'license': 'Project-generated teaching fixture',
                       'training_policy': 'Chronological train/validation/test splits; test predictions occur after model selection. Labels describe generated routing motifs.'}}
    add_examples(payload, rows, artifacts)
    output = Path(output) if output else root / 'frontend' / 'public' / 'model-demo.json'
    output.write_text(json.dumps(payload, indent=2) + '\n')
    (output.parent / 'demo-transfers.csv').write_bytes(source.read_bytes())
    export_runtime_bundle(root, payload, artifacts, source)
    print(json.dumps({'output': str(output), 'display_edges': len(graph['edges']),
                      'focus_margin': models['graphsage']['scores'][0]['score'], 'run_id': manifest['run_id']}))


def add_examples(payload, rows, artifacts):
    """Build judge-ready source views; predictions are recomputed by analyze_demo.

    These are different windows of the same disclosed teaching source, not new
    independent evaluation datasets. Labels and cached predictions are omitted.
    """
    from .graph import build_graph
    from .inference import score
    latest = datetime.fromisoformat(rows[-1]['timestamp']).replace(minute=0, second=0, microsecond=0)
    window = [row for row in rows if datetime.fromisoformat(row['timestamp']) >= latest]
    incoming = [row for row in window if row['from_account'] in {'0000', '0001'} and row['to_account'] == '0003']
    ordinary = [row for row in window if int(row['from_account']) >= 6 and int(row['to_account']) >= 6]
    groups = [
        ('incoming-collection', 'Two senders, one collector',
         'Inspect two generated transfers into Account 0003, with the earlier history actually sampled by GraphSAGE.', incoming),
        ('ordinary-payments', 'Ordinary payment comparison',
         'A comparison group of eight generated payments. Run either model to inspect its decisions; unflagged transfers are not certified safe.', ordinary),
        ('mixed-network', 'Routing and ordinary payments',
         'Compare all twelve transfers in the final source hour: four generated routing transfers alongside eight ordinary-payment motifs.', window),
    ]
    examples = [{
        'id': 'pass-through', 'title': payload['title'], 'description': payload['description'],
        'focus_edge_id': payload['focus_edge_id'], 'focus_edge_ids': [payload['focus_edge_id']],
        'graph': payload['graph'],
    }]
    for identifier, title, description, targets in groups:
        if not targets:
            raise ValueError('Example source does not contain the required synthetic window.')
        target_ids = {row['id'] for row in targets}
        display_ids = set(target_ids)
        for model in ('graphsage', 'graph_xgboost'):
            prediction = score(rows, artifacts, model, len(targets), target_ids=target_ids, explain=True)
            if {item['transaction_id'] for item in prediction['scores']} != target_ids:
                raise ValueError('Every example target must be eligible for both frozen models.')
        # Keep the initial graph readable. The inspector adds the selected
        # prediction's actual sampled history dynamically after live analysis.
        graph = build_graph([row for row in rows if row['id'] in display_ids])
        for node in graph['nodes']:
            node['label'] = 'Account ' + node['account']
        examples.append({'id': identifier, 'title': title, 'description': description,
                         'focus_edge_id': targets[0]['id'],
                         'focus_edge_ids': [row['id'] for row in targets], 'graph': graph})
    payload['examples'] = examples
    return payload


def refresh_examples():
    """Refresh views from packaged data/weights without training or changing weights."""
    from .parsers import parse_document
    data, artifacts, source = demo_paths()
    if not artifacts:
        raise ValueError('Install the packaged demo assets before refreshing examples.')
    rows = [dict(row, id=f'simulation:{index}', document_id='synthetic-transfers.csv', source_row=index)
            for index, row, error in parse_document(source, '.csv') if not error]
    if hashlib.sha256(source.read_bytes()).hexdigest() != data['source_sha256']:
        raise ValueError('Example source fingerprint mismatch.')
    add_examples(data, rows, artifacts)
    root = Path(__file__).parent.parent
    for output in (root / 'frontend/public/model-demo.json', Path(__file__).parent / 'demo_assets/model-demo.json'):
        output.write_text(json.dumps(data, indent=2) + '\n')
    return data


def export_runtime_bundle(root, payload, artifacts, source):
    """Ship small trained weights and source with deployments, not ignored var files."""
    target = root / 'dolos' / 'demo_assets'
    (target / 'artifacts').mkdir(parents=True, exist_ok=True)
    for name in ('manifest.json', 'graphsage.pt', 'graph_xgboost.json'):
        shutil.copyfile(artifacts / name, target / 'artifacts' / name)
    exported = artifacts / 'graphsage.npz'
    if exported.is_file():
        shutil.copyfile(exported, target / 'artifacts' / exported.name)
    else:
        # Offline conversion only. Serving reads these exact tensors with NumPy.
        import numpy as np
        import torch
        state = torch.load(artifacts / 'graphsage.pt', map_location='cpu', weights_only=True)
        np.savez_compressed(target / 'artifacts' / 'graphsage.npz',
                            **{name: value.detach().cpu().numpy() for name, value in state.items()})
    shutil.copyfile(source, target / 'synthetic-transfers.csv')
    (target / 'model-demo.json').write_text(json.dumps(payload, indent=2) + '\n')


def demo_paths():
    import os
    root = Path(__file__).parent.parent
    package = Path(__file__).parent / 'demo_assets'
    payload_path = package / 'model-demo.json'
    if not payload_path.is_file():
        payload_path = root / 'frontend' / 'public' / 'model-demo.json'
    if not payload_path.is_file():
        return None, None, None
    data = json.loads(payload_path.read_text())
    default_directory = package / 'artifacts' if (package / 'artifacts').is_dir() else root / 'var' / 'model-demo' / (data['source_sha256'][:12] + '-e' + str(data.get('epochs', 8)))
    directory = Path(os.environ.get('DOLOS_DEMO_MODEL_DIR', str(default_directory)))
    source = Path(os.environ.get('DOLOS_DEMO_SOURCE_CSV', str(package / 'synthetic-transfers.csv' if package.is_dir() else root / 'var' / 'model-demo' / 'synthetic-transfers.csv')))
    ready = directory if all((directory / name).is_file() for name in ('manifest.json', 'graphsage.npz', 'graph_xgboost.json')) and source.is_file() else None
    return data, ready, source


def analyze_demo(model, example=None):
    from .inference import score, ModelUnavailable
    from .parsers import parse_document
    data, directory, source = demo_paths()
    selected = next((item for item in (data or {}).get('examples', []) if item['id'] == example), None) if example else None
    if example and selected is None:
        raise ValueError('Unknown model example.')
    if not directory:
        raise ModelUnavailable('Live demonstration artifacts are not installed. Deploy the packaged demo_assets including graphsage.npz; no recorded prediction was substituted.')
    if hashlib.sha256(source.read_bytes()).hexdigest() != data['source_sha256']:
        raise ModelUnavailable('Demo source fingerprint differs from the recorded graph. Regenerate the demo.')
    manifest = json.loads((directory / 'manifest.json').read_text())
    if manifest.get('run_id') != data['run_id']:
        raise ModelUnavailable('Demo artifact does not match the displayed graph. Regenerate the demo.')
    rows = [dict(row, id=f'simulation:{index}', document_id='synthetic-transfers.csv', source_row=index)
            for index, row, error in parse_document(source, '.csv') if not error]
    targets = set(selected['focus_edge_ids']) if selected else {data['focus_edge_id']}
    result = score(rows, directory, model, len(targets), target_ids=targets, explain=True)
    return dict(result, status='completed', name=model, threshold=0)


if __name__ == '__main__':
    build_demo()
