"""The hybrid uses the original GraphSAGE encoder, not a surrogate embedding."""
import json
from pathlib import Path

import numpy as np
import pytest

from dolos.hybrid import make_features
from dolos.numpy_sage import encode_numpy
from dolos.parsers import parse_document

ROOT = Path(__file__).parents[1]
ARTIFACTS = ROOT / 'dolos/demo_assets/artifacts'


def test_numpy_embeddings_match_original_pytorch_encoder():
    torch = pytest.importorskip('torch')
    pytest.importorskip('torch_geometric')
    from dolos.model import DirectedSAGE
    rows = [dict(row, id=f'simulation:{index}')
            for index, row, error in parse_document(ROOT / 'dolos/demo_assets/synthetic-transfers.csv', '.csv') if not error]
    manifest = json.loads((ARTIFACTS / 'manifest.json').read_text())
    data, features = make_features(rows, manifest)
    target = np.flatnonzero(data.times >= manifest['selection_cutoff_us'])
    model = DirectedSAGE(features.node_dim, data.edge_features(target[:1]).shape[1])
    model.load_state_dict(torch.load(ARTIFACTS / 'graphsage.pt', weights_only=True, map_location='cpu'))
    model.eval()

    def original_embedding(chunk):
        roots, endpoints = features.roots(chunk)
        middle, inner = features.block(roots, 4)
        outer_keys, outer = features.block(middle, 2)
        values = torch.as_tensor(features.nodes(outer_keys))
        with torch.inference_mode():
            for layer, (indices, edges, _) in enumerate((outer, inner)):
                root = values[torch.as_tensor(indices)]
                values = (model.root[layer](root)
                          + model.incoming[layer]((values, root), torch.as_tensor(edges[0]))
                          + model.outgoing[layer]((values, root), torch.as_tensor(edges[1]))).relu()
            return values[torch.as_tensor(endpoints)].reshape(len(chunk), -1).numpy()

    expected = np.concatenate([original_embedding(target[start:start+384]) for start in range(0, len(target), 384)])
    actual = encode_numpy(features, target, ARTIFACTS / 'graphsage.npz')
    assert actual.shape == (432, 128)
    np.testing.assert_allclose(actual, expected, atol=1e-5, rtol=1e-5)
