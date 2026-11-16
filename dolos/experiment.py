"""Train one baseline and one final model.

The baseline uses transaction fields only. The final model uses GraphSAGE
embeddings and transaction fields in one XGBoost classifier.
"""
import hashlib
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np
import polars as pl
import torch
from xgboost import XGBClassifier

from .features import HOUR, FANOUTS, SnapshotFeatures
from .hybrid import train_hybrid
from .inference import score
from .model import SEED, fit_sage, metrics, package_version, prepare, read_ibm_csv
from .parsers import parse_document


def source_rows(path):
    """Keep each source row ID when the time order changes."""
    rows = []
    for index, row, error in parse_document(Path(path), '.csv'):
        if error:
            raise ValueError(f'Source row {index} is not valid: {error}')
        rows.append(dict(row, id=f'source:{index - 1}', source_row=index,
                         document_id=Path(path).name))
    rows.sort(key=lambda row: (row['timestamp'], row['source_row']))
    return rows


def run_experiment(source, directory, *, epochs=8, batch_size=192, data_kind='synthetic'):
    """Use the same test transfers for the baseline and the final model."""
    source, directory = Path(source), Path(directory)
    if epochs < 1 or batch_size < 2:
        raise ValueError('Use at least one epoch and a batch size of two or more.')
    if data_kind not in {'synthetic', 'ibm'}:
        raise ValueError('Set the data kind to synthetic or ibm.')
    if directory.exists():
        raise FileExistsError('Use a new output directory. Existing files must not change.')
    raw, fingerprint = read_ibm_csv(source)
    times = raw['timestamp'].cast(pl.Int64).sort().to_numpy()
    bounds = times[np.array([7 * len(times) // 10, 85 * len(times) // 100])] // HOUR * HOUR
    data = prepare(raw, fingerprint, bounds)
    features = SnapshotFeatures(data)
    train, validation, test = (np.arange(part.start, part.stop) for part in data.splits.values())
    if any(np.unique(data.labels[part]).size < 2 for part in (train, validation, test)):
        raise ValueError('Each data group must contain both label classes.')
    directory.mkdir(parents=True)
    torch.set_num_threads(4)

    # Fit the baseline with transaction fields. Do not use account history.
    baseline = XGBClassifier(n_estimators=150, max_depth=5, learning_rate=.08,
                             n_jobs=4, random_state=SEED, tree_method='hist')
    baseline.fit(data.edge_features(train), data.labels[train])
    baseline.save_model(directory / 'transaction_xgboost.json')

    # Use validation labels only to select the encoder checkpoint.
    log = io.StringIO()
    with redirect_stdout(log):
        encoder, chosen_epoch = fit_sage(features, train, validation, neighbors=True,
                                         epochs=epochs, batch_size=batch_size)
    (directory / 'encoder-training.log').write_text(log.getvalue())
    state = encoder.state_dict()
    torch.save(state, directory / 'graphsage.pt')
    np.savez_compressed(directory / 'graphsage.npz',
                       **{name: value.detach().cpu().numpy() for name, value in state.items()})
    run_id = f'{fingerprint[:12]}-notebook-{len(data.ids)}-e{epochs}'
    manifest = {
        'runtime': {name: package_version(name) for name in
                    ('numpy', 'polars', 'torch', 'torch-geometric', 'scikit-learn', 'xgboost')},
        'run_id': run_id, 'source_rows': len(data.ids), 'source_sha256': fingerprint,
        'feature_version': 'hourly-v1', 'seed': SEED, 'bucket_us': HOUR,
        'training_cutoff_us': int(bounds[0]), 'selection_cutoff_us': int(bounds[1]),
        'validation_start_us': int(bounds[0]), 'test_start_us': int(bounds[1]),
        'vocabularies': data.vocabularies, 'fanouts_per_direction': FANOUTS,
        'epochs': epochs, 'selected_epochs': {'encoder': chosen_epoch},
        'data_kind': data_kind,
        'splits': {name: {'rows': int(part.stop - part.start),
                          'positives': int(data.labels[part].sum())}
                   for name, part in data.splits.items()},
        'artifact_sha256': {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                            for path in directory.iterdir() if path.suffix in {'.pt', '.npz', '.json'}},
    }
    (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')

    # Keep the encoder fixed. Fit one final XGBoost classifier.
    hybrid_metadata = train_hybrid(directory, source)
    rows = source_rows(source)
    labels = {f'source:{int(identifier)}': int(label)
              for identifier, label in zip(data.ids, data.labels)}
    target_ids = {f'source:{int(identifier)}' for identifier in data.ids[test]}
    results, predictions = {}, {}
    for name in ('transaction_xgboost', 'hybrid'):
        prediction = score(rows, directory, name, len(test), target_ids=target_ids, explain=True)
        if {item['transaction_id'] for item in prediction['scores']} != target_ids:
            raise AssertionError('The models must score the same test transfers.')
        ordered = sorted(prediction['scores'], key=lambda item: int(item['transaction_id'].split(':')[1]))
        ids = np.array([int(item['transaction_id'].split(':')[1]) for item in ordered])
        actual = np.array([labels[item['transaction_id']] for item in ordered], dtype=np.uint8)
        margins = np.array([item['score'] for item in ordered])
        results[name] = metrics(ids, actual, margins)
        results[name]['positive_decisions'] = int((margins > 0).sum())
        predictions[name] = prediction
    report = {'data_kind': data_kind, 'source_sha256': fingerprint, 'source_rows': len(rows),
              'splits': manifest['splits'], 'selected_encoder_epoch': chosen_epoch,
              'models': results, 'hybrid_run_id': hybrid_metadata['run_id'],
              'evaluation_note': ('The data use the same generator as training. This test does not measure real-world performance.'
                                  if data_kind == 'synthetic' else
                                  'This test uses IBM data. The results do not measure performance on real bank statements.')}
    (directory / 'comparison.json').write_text(json.dumps(report, indent=2) + '\n')
    (directory / 'test-predictions.json').write_text(json.dumps(predictions, indent=2) + '\n')
    return report, predictions
