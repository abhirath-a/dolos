"""Every returned discovery has exact group credits, beyond detailed top-ten explanations."""
from pathlib import Path

import pytest

from dolos.hybrid import score_hybrid
from dolos.parsers import parse_document


def test_every_held_out_hybrid_score_has_summing_component_credits():
    root = Path(__file__).parents[1]
    rows = [dict(row, id=f'simulation:{index}', document_id='synthetic-transfers.csv', source_row=index)
            for index, row, error in parse_document(root / 'dolos/demo_assets/synthetic-transfers.csv', '.csv') if not error]
    result = score_hybrid(rows, root / 'dolos/demo_assets/artifacts', 1000, explain=True)
    assert len(result['scores']) == 432
    assert sum(item['explanation'] is not None for item in result['scores']) == 10
    for item in result['scores']:
        assert len(item['component_contributions']) == 2
        credit = sum(group['contribution'] for group in item['component_contributions'])
        assert credit == pytest.approx(item['contribution_sum'])
        assert item['base_margin'] + credit == pytest.approx(item['score'], abs=1e-4)
