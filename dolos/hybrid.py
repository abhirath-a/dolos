"""One trained GraphSAGE encoder -> XGBoost decision pipeline.

GraphSAGE's two layers encode historical directed neighborhoods. A single native
XGBoost model consumes those embeddings and direct transaction fields. Grouped
TreeSHAP credits feature blocks in that final model; they are not causal credit.
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np

from .features import CATEGORICAL, History, Transactions, SnapshotFeatures
from .numpy_sage import encode_numpy


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_features(rows, manifest):
    vocab = manifest['vocabularies']
    times = np.array([round(datetime.fromisoformat(row['timestamp']).timestamp() * 1_000_000)
                      for row in rows], dtype=np.int64)
    if np.any(times[1:] < times[:-1]):
        raise ValueError('Hybrid inference requires chronological history.')
    accounts = sorted({row[key] for row in rows for key in ('sender_id', 'receiver_id')})
    mapping = {value: i for i, value in enumerate(accounts)}
    sender = np.array([mapping[row['sender_id']] for row in rows], dtype=np.int32)
    receiver = np.array([mapping[row['receiver_id']] for row in rows], dtype=np.int32)
    categories = []
    for name in CATEGORICAL:
        codes = {value: i + 1 for i, value in enumerate(vocab[name])}
        categories.append([codes.get(row[name], 0) for row in rows])
    amounts = np.array([[float(row['amount_received']), float(row['amount_paid'])] for row in rows], dtype=np.float32)
    amounts = np.sign(amounts) * np.log1p(np.abs(amounts))
    data = Transactions(np.arange(len(rows)), times, sender, receiver, np.zeros(len(rows), dtype=np.uint8),
                        amounts, np.array(categories, dtype=np.int32).T, vocab, {},
                        History.build(sender, receiver, times, len(accounts)),
                        History.build(receiver, sender, times, len(accounts)), 'hybrid-serving')
    return data, SnapshotFeatures(data)


def feature_names(manifest):
    names = [f'GraphSAGE {side} embedding {index + 1}' for side in ('sender', 'receiver') for index in range(64)]
    names.extend(('Log received amount', 'Log paid amount'))
    for category in CATEGORICAL:
        names.extend(category.replace('_', ' ') + ': ' + value for value in ['unknown'] + manifest['vocabularies'][category])
    return names


def matrix(features, rows, directory):
    return np.concatenate((encode_numpy(features, rows, Path(directory) / 'graphsage.npz'),
                           features.data.edge_features(rows)), axis=1)


def context_explanation(features, position, rows):
    roots, _ = features.roots(np.array([position]))
    middle, first = features.block(roots, 4)
    _, second = features.block(middle, 2)
    first_rows = {int(index) for group in first[2] for index in group}
    sampled = first_rows | {int(index) for group in second[2] for index in group}
    return {'context_transaction_ids': [rows[i]['id'] for i in sorted(sampled)],
            'context_transactions': [dict(rows[i], hop=1 if i in first_rows else 2) for i in sorted(sampled)],
            'cutoff_us': int(features.cutoffs[position])}


def score_hybrid(rows, directory, limit, target_ids=None, explain=False):
    from xgboost import Booster, DMatrix
    from .inference import ModelUnavailable, tree_paths
    directory = Path(directory)
    required = ('manifest.json', 'hybrid_manifest.json', 'hybrid_xgboost.json', 'graphsage.npz')
    if not all((directory / name).is_file() for name in required):
        raise ModelUnavailable('Hybrid artifacts are missing. Deploy the GraphSAGE encoder, hybrid XGBoost model and both manifests together.')
    manifest = json.loads((directory / 'manifest.json').read_text())
    metadata = json.loads((directory / 'hybrid_manifest.json').read_text())
    if metadata['feature_version'] != 'sage-embedding-xgb-v1' or metadata['base_run_id'] != manifest['run_id']:
        raise ModelUnavailable('Hybrid feature schema or encoder run does not match the base manifest.')
    if metadata['encoder_sha256'] != digest(directory / 'graphsage.npz') or metadata['tree_sha256'] != digest(directory / 'hybrid_xgboost.json'):
        raise ModelUnavailable('Hybrid artifact checksum mismatch; deploy the matching exported model files.')
    if metadata['source_sha256'] != manifest['source_sha256'] or metadata['selection_cutoff_us'] != manifest['selection_cutoff_us']:
        raise ModelUnavailable('Hybrid training provenance does not match its encoder manifest.')
    result = {'model': 'hybrid', 'run_id': metadata['run_id'], 'score_type': 'raw_margin',
              'history_rows': len(rows), 'scores': []}
    if not rows:
        return result
    data, features = make_features(rows, manifest)
    eligible = np.flatnonzero(data.times >= metadata['selection_cutoff_us'])
    if target_ids is not None:
        eligible = np.array([index for index in eligible if rows[index]['id'] in target_ids], dtype=np.int64)
    targets = eligible[-limit:]
    if not len(targets):
        return dict(result, reason='No transactions after model selection cutoff.')
    inputs = matrix(features, targets, directory)
    if inputs.shape[1] != metadata['feature_count']:
        raise ModelUnavailable('Hybrid feature width differs from its trained artifact.')
    booster = Booster(params={'nthread': 1})
    booster.load_model(str(directory / 'hybrid_xgboost.json'))
    values = booster.predict(DMatrix(inputs, nthread=1), output_margin=True)
    explanations = {}
    score_credits = {}
    if explain:
        # Every returned transfer gets exact group credits. Only the expensive
        # detailed tree paths/context are limited to the top ten review ranks.
        contributions = booster.predict(DMatrix(inputs, nthread=1), pred_contribs=True)
        for position, attribution in enumerate(contributions):
            graph_credit = float(attribution[:128].sum(dtype=np.float64))
            direct_credit = float(attribution[128:-1].sum(dtype=np.float64))
            score_credits[rows[targets[position]]['id']] = {
                'base_margin': float(attribution[-1]),
                'contribution_sum': graph_credit + direct_credit,
                'component_contributions': [
                    {'name': 'GraphSAGE neighborhood', 'contribution': graph_credit},
                    {'name': 'Transaction fields', 'contribution': direct_credit}]}
        ranked = np.argsort(-values)[:10]
        names = feature_names(manifest)
        for position in ranked:
            attribution = contributions[position]
            top = np.argsort(-np.abs(attribution[:-1]))[:8]
            explanations[rows[targets[position]]['id']] = {
                'kind': 'hybrid', **score_credits[rows[targets[position]]['id']],
                'tree_count': len(booster.get_dump()),
                'tree_paths': tree_paths(booster, inputs[position], names),
                'features': [{'name': names[index], 'value': float(inputs[position, index]),
                              'contribution': float(attribution[index])} for index in top],
                **context_explanation(features, targets[position], rows),
                'note': 'One final XGBoost decision uses GraphSAGE embeddings and direct transaction fields. '
                        'Grouped TreeSHAP contributions plus the base margin sum to the final raw margin. '
                        'They attribute influence in the final model, not causal responsibility. '
                        'Historical neighbors are actual GraphSAGE context, not individually causal attributions.'}
    result['scores'] = sorted([
        {'transaction_id': rows[index]['id'], 'score': float(value),
         'context_cutoff_us': int(features.cutoffs[index]),
         **score_credits.get(rows[index]['id'], {}),
         'explanation': explanations.get(rows[index]['id'])}
        for index, value in zip(targets, values)], key=lambda item: (-item['score'], item['transaction_id']))
    return result


def read_labeled_rows(source):
    """Keep each label with its source row when the time order changes."""
    import csv
    from .parsers import parse_document
    source = Path(source)
    with source.open(encoding='utf-8-sig', newline='') as file:
        reader = csv.reader(file)
        header = next(reader, [])
        columns = [i for i, name in enumerate(header) if name in ('Is Laundering', 'is_laundering')]
        if len(columns) != 1:
            raise ValueError('Training data must have one laundering label column.')
        records = [row for row in reader if row and any(value.strip() for value in row)]
    parsed = list(parse_document(source, '.csv'))
    if len(parsed) != len(records) or any(error or row is None for _, row, error in parsed):
        raise ValueError('Training data has invalid rows. Correct the rows before training.')
    pairs = []
    for (index, row, _), record in zip(parsed, records):
        label = int(record[columns[0]])
        if label not in (0, 1):
            raise ValueError('Training labels must be 0 or 1.')
        pairs.append((dict(row, id=f'simulation:{index}', document_id=source.name, source_row=index), label))
    pairs.sort(key=lambda item: (item[0]['timestamp'], item[0]['source_row']))
    return [row for row, _ in pairs], np.array([label for _, label in pairs], dtype=np.uint8)


def train_hybrid(directory, source):
    """Offline fit of the single final classifier; never invoked during serving."""
    from xgboost import XGBClassifier
    from sklearn.metrics import average_precision_score
    directory, source = Path(directory), Path(source)
    manifest = json.loads((directory / 'manifest.json').read_text())
    if digest(source) != manifest['source_sha256']:
        raise ValueError('Source differs from the frozen encoder training source.')
    rows, labels = read_labeled_rows(source)
    data, features = make_features(rows, manifest)
    train = np.flatnonzero(data.times < manifest['training_cutoff_us'])
    test = np.flatnonzero(data.times >= manifest['selection_cutoff_us'])
    classifier = XGBClassifier(n_estimators=150, max_depth=5, learning_rate=.08,
                               n_jobs=4, random_state=7, tree_method='hist')
    classifier.fit(matrix(features, train, directory), labels[train])
    artifact = directory / 'hybrid_xgboost.json'
    classifier.save_model(artifact)
    inputs = matrix(features, test, directory)
    margins = classifier.predict(inputs, output_margin=True)
    metadata = {'feature_version': 'sage-embedding-xgb-v1',
                'run_id': manifest['run_id'] + '-hybrid-v1', 'base_run_id': manifest['run_id'],
                'source_sha256': digest(source), 'encoder_sha256': digest(directory / 'graphsage.npz'),
                'tree_sha256': digest(artifact), 'training_cutoff_us': manifest['training_cutoff_us'],
                'selection_cutoff_us': manifest['selection_cutoff_us'], 'source_rows': len(rows),
                'training_rows': len(train), 'held_out_rows': len(test), 'feature_count': inputs.shape[1],
                'graph_embedding_features': 128, 'transaction_features': inputs.shape[1] - 128,
                'architecture': 'Frozen directed GraphSAGE sender/receiver embeddings plus transaction fields -> one XGBoost classifier',
                'training_policy': 'Frozen encoder trained before training cutoff and checkpoint selected on chronological validation. Final XGBoost trained only on pre-training-cutoff rows with fixed hyperparameters; held-out labels used only for this reported evaluation.',
                'parameters': {'n_estimators': 150, 'max_depth': 5, 'learning_rate': .08, 'random_state': 7},
                'held_out_evaluation': {'average_precision': float(average_precision_score(labels[test], margins)),
                                        'positive_decisions': int((margins > 0).sum()),
                                        'positive_labels': int(labels[test].sum()),
                                        'note': 'Same synthetic generator as training; not independent real-world validation.'}}
    (directory / 'hybrid_manifest.json').write_text(json.dumps(metadata, indent=2) + '\n')
    return metadata

