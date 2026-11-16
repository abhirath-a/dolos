import csv

import pytest

from dolos.hybrid import read_labeled_rows
from dolos.parsers import FIELDS


def test_training_sort_keeps_labels_and_source_rows_together(tmp_path):
    path = tmp_path / 'unsorted.csv'
    with path.open('w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow([*FIELDS, 'is_laundering', 'reference'])
        writer.writerow(['2025-01-02', 'D', '01', 'D', '02', '900', 'USD', '900', 'USD', 'WIRE', 1, 'second'])
        writer.writerow(['2025-01-01', 'D', '03', 'D', '04', '10', 'USD', '10', 'USD', 'WIRE', 0, 'first'])
    rows, labels = read_labeled_rows(path)
    assert labels.tolist() == [0, 1]
    assert [row['source_row'] for row in rows] == [2, 1]
    assert [row['reference'] for row in rows] == ['first', 'second']
    assert [row['amount_paid'] for row in rows] == ['10', '900']


def test_training_does_not_silently_drop_invalid_rows(tmp_path):
    path = tmp_path / 'invalid.csv'
    path.write_text(','.join([*FIELDS, 'is_laundering']) + '\nwrong,row\n')
    with pytest.raises(ValueError, match='invalid rows'):
        read_labeled_rows(path)
