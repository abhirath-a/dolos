"""Small actual training round trip; the full IBM benchmark is intentionally separate."""
import csv
from datetime import datetime, timedelta

import pytest

pytest.importorskip('torch')
pytest.importorskip('polars')
pytest.importorskip('xgboost')

from dolos.inference import score
from dolos.model import run, IBM_HEADER
from dolos.parsers import parse_document


def test_training_export_and_serving_use_identical_features(tmp_path):
    import numpy as np
    import polars as pl
    fixture = tmp_path / 'fixture.csv'
    start = datetime(2022, 9, 1)
    with fixture.open('w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(IBM_HEADER)
        for i in range(720):
            writer.writerow([(start + timedelta(minutes=i // 3)).strftime('%Y/%m/%d %H:%M'),
                             '001', f'{i % 13:04x}', '002', f'{i % 17:04x}', i + 1,
                             'US Dollar', i + 1, 'US Dollar', 'ACH', int(i % 17 == 0)])
    artifacts = tmp_path / 'model'
    run(fixture, artifacts, epochs=1, batch_size=128)
    rows = [dict(row, id=f'tx-{index:04d}') for index, row, error in parse_document(fixture, '.csv')]
    for name in ('graph_xgboost', 'transaction_xgboost', 'graphsage', 'graphsage_no_neighbors'):
        expected = pl.read_parquet(artifacts / (name + '.parquet'))['score'].to_numpy()
        actual = score(rows, artifacts, name, 1000)
        by_id = {r['transaction_id']: r['score'] for r in actual['scores']}
        ordered = [by_id[r['id']] for r in rows[-len(expected):]]
        np.testing.assert_allclose(ordered, expected, atol=1e-5, rtol=1e-5)
        explained = score(rows, artifacts, name, 1000, target_ids=set(by_id), explain=True)
        if 'xgboost' in name:
            for result in explained['scores']:
                detail = result['explanation']
                if detail is None:
                    continue
                assert detail['kind'] == 'tree_shap'
                assert all(not f['name'].startswith('Feature ') for f in detail['features'])
                assert abs(detail['base_margin'] + detail['contribution_sum'] - result['score']) < 1e-4
                assert detail['tree_paths'] and detail['tree_count'] == 150
        elif name == 'graphsage':
            by_row = {row['id']: row for row in rows}
            for result in explained['scores']:
                detail = result['explanation']
                if detail is None:
                    continue
                assert detail['kind'] == 'graph_context'
                for transaction_id in detail['context_transaction_ids']:
                    from datetime import timezone
                    at = datetime.fromisoformat(by_row[transaction_id]['timestamp'])
                    assert at.timestamp() * 1_000_000 < result['context_cutoff_us']
        # Same-hour and future transfers must not influence earlier scores.
        future = dict(rows[-1], id='future', timestamp='2023-01-01T00:00:00.000000+00:00', amount_paid='999999')
        changed = score(rows + [future], artifacts, name, 1000)
        changed_by_id = {r['transaction_id']: r['score'] for r in changed['scores']}
        np.testing.assert_allclose([changed_by_id[k] for k in by_id], list(by_id.values()), atol=1e-5)
