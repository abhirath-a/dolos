"""Check the notebook model list, execution state, and sentence lengths.

These checks do not replace a full STE dictionary review.
"""
import json
import re
from pathlib import Path


def test_notebook_has_one_baseline_one_final_model_and_no_cell_errors():
    notebook = json.loads(Path('dolos.ipynb').read_text())
    comparison = notebook['metadata']['dolos_comparison']
    assert comparison['baseline'] == 'transaction_xgboost'
    assert comparison['final_model'] == 'hybrid'
    assert notebook['metadata']['dolos_language']['standard'] == 'ASD-STE100'
    assert notebook['metadata']['dolos_language']['issue'] == 9
    code_cells = [cell for cell in notebook['cells'] if cell['cell_type'] == 'code']
    assert code_cells and all(cell['execution_count'] for cell in code_cells)
    assert not any(output['output_type'] == 'error' for cell in code_cells for output in cell.get('outputs', []))


def test_notebook_description_sentences_have_no_more_than_25_words():
    notebook = json.loads(Path('dolos.ipynb').read_text())
    for cell in notebook['cells']:
        if cell['cell_type'] != 'markdown':
            continue
        text = ''.join(cell['source'])
        text = re.sub(r'```.*?```', '', text, flags=re.S)
        text = re.sub(r'\[([^]]+)\]\([^)]+\)', r'\1', text)
        for line in text.splitlines():
            if not line.strip() or line.startswith(('#', '|', '- ')):
                continue
            for sentence in re.split(r'(?<=[.!?])\s+', line):
                limit = 20 if re.match(r'^\d+\.\s', line) else 25
                assert len(sentence.split()) <= limit, sentence
