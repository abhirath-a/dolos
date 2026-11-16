"""Check the two-model notebook experiment."""
import csv
import json
from pathlib import Path

import pytest

pytest.importorskip('torch')
pytest.importorskip('polars')
pytest.importorskip('sklearn')

from dolos.experiment import run_experiment


@pytest.mark.parametrize('reverse_rows', [False, True])
def test_only_baseline_and_final_share_test_transfers(tmp_path, reverse_rows):
    source = Path('dolos/demo_assets/synthetic-transfers.csv')
    with source.open() as file:
        header, *rows = list(csv.reader(file))
    rows = rows[:720]
    if reverse_rows:
        rows.reverse()
    fixture = tmp_path / 'source.csv'
    with fixture.open('w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(header)
        writer.writerows(rows)
    directory = tmp_path / 'run'
    report, predictions = run_experiment(fixture, directory, epochs=1)
    assert set(report['models']) == {'transaction_xgboost', 'hybrid'}
    assert report['source_rows'] == 720
    assert report['splits']['test']['rows'] == 108
    target_sets = [{row['transaction_id'] for row in result['scores']} for result in predictions.values()]
    assert target_sets[0] == target_sets[1]
    assert all(values['rows'] == 108 for values in report['models'].values())
    assert not (directory / 'graph_xgboost.json').exists()
    assert (directory / 'hybrid_xgboost.json').is_file()
    manifest = json.loads((directory / 'manifest.json').read_text())
    assert manifest['data_kind'] == 'synthetic'
    for row in predictions['hybrid']['scores']:
        total = row['base_margin'] + sum(item['contribution'] for item in row['component_contributions'])
        assert total == pytest.approx(row['score'], abs=1e-4)
    with pytest.raises(FileExistsError):
        run_experiment(fixture, directory, epochs=1)
