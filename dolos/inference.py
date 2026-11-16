"""Inference with the notebook's exact hourly features and frozen training vocabulary."""
import json
from pathlib import Path


class ModelUnavailable(RuntimeError):
    pass


def score(rows, directory, model_name, limit, target_ids=None, explain=False):
    if not directory or not (Path(directory) / 'manifest.json').is_file():
        raise ModelUnavailable('No trained artifact configured. Run dolos-train and set DOLOS_MODEL_DIR.')
    if model_name == 'hybrid':
        from .hybrid import score_hybrid
        return score_hybrid(rows, directory, limit, target_ids=target_ids, explain=explain)
    try:
        import numpy as np
        from .features import Transactions, History, SnapshotFeatures, CATEGORICAL
    except ImportError as exc:
        raise ModelUnavailable('Inference runtime is missing. Reinstall the base dolos dependencies and redeploy.') from exc
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
    if target_ids is not None:
        eligible = np.array([i for i in eligible if rows[i]['id'] in target_ids], dtype=np.int64)
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
    explanations = {}
    if model_name in ('graph_xgboost', 'transaction_xgboost'):
        from xgboost import Booster, DMatrix
        model = Booster(params={'nthread': 1})
        artifact = directory / (model_name + '.json')
        if not artifact.is_file():
            raise ModelUnavailable(f'Missing {model_name} artifact.')
        model.load_model(str(artifact))
        inputs = data.edge_features(target)
        if model_name == 'graph_xgboost':
            roots, endpoints = features.roots(target)
            inputs = np.column_stack((inputs, features.nodes(roots)[endpoints].reshape(len(target), -1)))
        values = model.predict(DMatrix(inputs, nthread=1), output_margin=True)
        if explain:
            ranked = np.argsort(-values)[:10]
            contributions = model.predict(DMatrix(inputs[ranked], nthread=1), pred_contribs=True)
            names = ['Log received amount', 'Log paid amount']
            for category in CATEGORICAL:
                names.extend([category.replace('_', ' ') + ': ' + label for label in ['unknown'] + vocab[category]])
            # SnapshotFeatures packs six temporal features and four features per currency, per direction.
            if model_name == 'graph_xgboost':
                node_names = []
                for direction, currency_key in [('outgoing', 'payment_currency'), ('incoming', 'receiving_currency')]:
                    node_names.extend([direction + ' ' + label for label in
                        ['log count', 'history present', 'log hours since last', 'log history span', 'log recent count', 'log reference rate']])
                    for measure in ['log count', 'signed log total', 'signed log mean', 'log standard deviation']:
                        node_names.extend([direction + ' ' + code + ' ' + measure for code in vocab[currency_key]])
                names.extend([side + ' ' + label for side in ['Sender', 'Receiver'] for label in node_names])
            if len(names) != inputs.shape[1]:
                names = ['Feature ' + str(index) for index in range(inputs.shape[1])]
            for position, contrib in zip(ranked, contributions):
                top = np.argsort(-np.abs(contrib[:-1]))[:8]
                explanations[rows[target[position]]['id']] = {'kind': 'tree_shap',
                    'base_margin': float(contrib[-1]), 'contribution_sum': float(contrib[:-1].sum()),
                    'tree_count': len(model.get_dump()),
                    'tree_paths': tree_paths(model, inputs[position], names),
                    'features': [{'name': names[j], 'value': float(inputs[position, j]), 'contribution': float(contrib[j])} for j in top],
                    'note': 'TreeSHAP contributions add to the raw margin with the base value. Top eight shown; omitted features still contribute. Values are transformed model inputs, not raw statement amounts. These are model attributions, not causal evidence.'}
    else:
        artifact = directory / (model_name + '.npz')
        if artifact.is_file():
            from .numpy_sage import predict_numpy
            values = predict_numpy(features, target, artifact, neighbors=model_name == 'graphsage')
        else:
            # Older training runs can still serve in the optional full ML environment.
            try:
                import torch
                from .model import DirectedSAGE, predict
            except ImportError as exc:
                raise ModelUnavailable('GraphSAGE needs an exported .npz weight file. Regenerate/export the model offline and redeploy; inference does not require the training extra.') from exc
            artifact = directory / (model_name + '.pt')
            if not artifact.is_file():
                raise ModelUnavailable(f'Missing {model_name} artifact.')
            model = DirectedSAGE(features.node_dim, data.edge_features(target[:1]).shape[1],
                                 neighbors=model_name == 'graphsage')
            model.load_state_dict(torch.load(artifact, map_location='cpu', weights_only=True))
            values = predict(model, features, target, 384)
        if explain and model_name == 'graphsage':
            for position in np.argsort(-values)[:10]:
                roots, _ = features.roots(target[position:position+1])
                middle, first = features.block(roots, 4)
                _, second = features.block(middle, 2)
                sampled = set(int(i) for block in (first, second) for group in block[2] for i in group)
                explanations[rows[target[position]]['id']] = {'kind': 'graph_context',
                    'context_transaction_ids': [rows[i]['id'] for i in sorted(sampled)],
                    'context_transactions': [dict(rows[i], hop=1 if i in set(int(j) for group in first[2] for j in group) else 2) for i in sorted(sampled)],
                    'cutoff_us': int(features.cutoffs[target[position]]),
                    'note': 'Actual historical neighbors sampled for the two GraphSAGE layers. Context membership is not a causal attribution.'}
    return {'model': model_name, 'run_id': manifest['run_id'], 'score_type': 'raw_margin',
            'history_rows': len(rows), 'scores': sorted([
                {'transaction_id': rows[i]['id'], 'score': float(value),
                 'context_cutoff_us': int(features.cutoffs[i]), 'explanation': explanations.get(rows[i]['id'])} for i, value in zip(target, values)
            ], key=lambda r: (-r['score'], r['transaction_id']))}


def tree_paths(booster, inputs, names, count=3):
    """Actual branch decisions in the first trees; not a surrogate explanation."""
    paths = []
    for index, encoded in enumerate(booster.get_dump(dump_format='json')[:count]):
        node = json.loads(encoded)
        steps = []
        while 'leaf' not in node:
            feature_index = int(node['split'].removeprefix('f'))
            value = float(inputs[feature_index])
            threshold = node['split_condition']
            lower = value < threshold
            steps.append({'feature': names[feature_index], 'value': value,
                          'threshold': threshold, 'branch': 'less_than' if lower else 'greater_or_equal'})
            child_id = node['yes'] if lower else node['no']
            node = next(child for child in node['children'] if child['nodeid'] == child_id)
        paths.append({'tree': index + 1, 'steps': steps, 'leaf_margin': node['leaf']})
    return paths
