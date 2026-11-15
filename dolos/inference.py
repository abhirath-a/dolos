"""Inference with the notebook's exact hourly features and frozen training vocabulary."""
import json
from pathlib import Path


class ModelUnavailable(RuntimeError):
    pass


def score(rows, directory, model_name, limit):
    if not directory or not (Path(directory) / 'manifest.json').is_file():
        raise ModelUnavailable('No trained artifact configured. Run dolos-train and set DOLOS_MODEL_DIR.')
    try:
        import numpy as np
        from .model import Transactions, History, SnapshotFeatures, DirectedSAGE, predict, CATEGORICAL
    except ImportError as exc:
        raise ModelUnavailable('Install dolos[ml] to enable model inference.') from exc
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text())
    if manifest.get('feature_version') != 'hourly-v1' or 'vocabularies' not in manifest:
        raise ModelUnavailable('Artifact lacks frozen vocabulary metadata; retrain with dolos-train.')
    if not rows:
        return {'model': model_name, 'scores': []}
    vocab = manifest['vocabularies']
    # Only post-selection transactions can be scored with this frozen artifact.
    from datetime import datetime
    times = np.array([round(datetime.fromisoformat(r['timestamp']).timestamp() * 1_000_000) for r in rows], dtype=np.int64)
    eligible = np.flatnonzero(times >= manifest['selection_cutoff_us'])
    target = eligible[-limit:]
    if not len(target):
        return {'model': model_name, 'scores': [], 'reason': 'No transactions after model selection cutoff.'}
    accounts = sorted({r[k] for r in rows for k in ('sender_id', 'receiver_id')})
    mapping = {a: i for i, a in enumerate(accounts)}
    sender = np.array([mapping[r['sender_id']] for r in rows], dtype=np.int32)
    receiver = np.array([mapping[r['receiver_id']] for r in rows], dtype=np.int32)
    categories = []
    for name in CATEGORICAL:
        codes = {value: i + 1 for i, value in enumerate(vocab[name])}
        categories.append([codes.get(r[name], 0) for r in rows])
    amounts = np.array([[float(r['amount_received']), float(r['amount_paid'])] for r in rows], dtype=np.float32)
    amounts = np.sign(amounts) * np.log1p(np.abs(amounts))
    data = Transactions(np.arange(len(rows)), times, sender, receiver, np.zeros(len(rows), dtype=np.uint8),
                        amounts, np.array(categories, dtype=np.int32).T, vocab, {},
                        History.build(sender, receiver, times, len(accounts)),
                        History.build(receiver, sender, times, len(accounts)), 'api')
    features = SnapshotFeatures(data)
    if model_name in ('graph_xgboost', 'transaction_xgboost'):
        from xgboost import XGBClassifier
        model = XGBClassifier()
        artifact = directory / (model_name + '.json')
        if not artifact.is_file():
            raise ModelUnavailable(f'Missing {model_name} artifact.')
        model.load_model(artifact)
        inputs = data.edge_features(target)
        if model_name == 'graph_xgboost':
            roots, endpoints = features.roots(target)
            inputs = np.column_stack((inputs, features.nodes(roots)[endpoints].reshape(len(target), -1)))
        values = model.predict(inputs, output_margin=True)
    else:
        import torch
        artifact = directory / (model_name + '.pt')
        if not artifact.is_file():
            raise ModelUnavailable(f'Missing {model_name} artifact.')
        model = DirectedSAGE(features.node_dim, data.edge_features(target[:1]).shape[1],
                             neighbors=model_name == 'graphsage')
        model.load_state_dict(torch.load(artifact, map_location='cpu', weights_only=True))
        values = predict(model, features, target, 384)
    return {'model': model_name, 'run_id': manifest['run_id'], 'score_type': 'raw_margin',
            'history_rows': len(rows), 'scores': sorted([
                {'transaction_id': rows[i]['id'], 'score': float(value),
                 'context_cutoff_us': int(features.cutoffs[i])} for i, value in zip(target, values)
            ], key=lambda r: (-r['score'], r['transaction_id']))}
