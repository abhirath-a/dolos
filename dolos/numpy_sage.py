"""Evaluation-only execution of the trained DirectedSAGE network with NumPy.

This evaluates the exported trained tensors, including directed mean aggregation,
with the same sampled historical neighborhoods as the PyTorch training model.
It does not approximate predictions or read previously calculated scores.
"""
from pathlib import Path

import numpy as np

from .features import FANOUTS


def _linear(values, weights, prefix, bias=True):
    matrix = weights[prefix + '.weight']
    if matrix.ndim != 2 or values.shape[1] != matrix.shape[1]:
        raise ValueError('GraphSAGE tensor dimensions do not match the feature schema.')
    result = np.einsum('ij,kj->ik', values, matrix, optimize=False)
    if bias:
        result = result + weights[prefix + '.bias']
    return result


def _mean_neighbors(values, edges, count):
    source, target = edges
    sums = np.zeros((count, values.shape[1]), dtype=np.float32)
    np.add.at(sums, target, values[source])
    divisor = np.maximum(np.bincount(target, minlength=count), 1).astype(np.float32)
    return sums / divisor[:, None]


def _encode(features, rows, weights, neighbors):
    roots, endpoints = features.roots(rows)
    if neighbors:
        middle, inner = features.block(roots, FANOUTS[0])
        outer_keys, outer = features.block(middle, FANOUTS[1])
        values = features.nodes(outer_keys)
        for layer, (target, edges, _) in enumerate((outer, inner)):
            root = values[target]
            incoming = _mean_neighbors(values, edges[0], len(target))
            outgoing = _mean_neighbors(values, edges[1], len(target))
            values = np.maximum(
                _linear(root, weights, f'root.{layer}')
                + _linear(incoming, weights, f'incoming.{layer}.lin_l', bias=False)
                + _linear(outgoing, weights, f'outgoing.{layer}.lin_l', bias=False),
                0,
            )
    else:
        values = features.nodes(roots)
        for layer in range(2):
            values = np.maximum(_linear(values, weights, f'root.{layer}'), 0)
    return values[endpoints].reshape(len(rows), -1)


def _score(features, rows, weights, neighbors):
    joined = np.concatenate((_encode(features, rows, weights, neighbors), features.data.edge_features(rows)), axis=1)
    hidden = np.maximum(_linear(joined, weights, 'head.0'), 0)
    # PyTorch's head.2 is dropout, disabled in evaluation mode.
    return _linear(hidden, weights, 'head.3').reshape(-1)


def _weights(weight_path):
    with np.load(Path(weight_path), allow_pickle=False) as archive:
        weights = {key: archive[key] for key in archive.files}
    if any(array.dtype != np.float32 or not np.isfinite(array).all()
           for array in weights.values()):
        raise ValueError('Expected finite float32 GraphSAGE tensors.')
    return weights


def encode_numpy(features, target, weight_path, neighbors=True, batch_size=384):
    """Encode sender/receiver to 128 trained GraphSAGE features, without its head."""
    if batch_size < 1:
        raise ValueError('GraphSAGE batch size must be positive.')
    if not len(target):
        return np.empty((0, 128), dtype=np.float32)
    weights = _weights(weight_path)
    return np.concatenate([
        _encode(features, target[start:start + batch_size], weights, neighbors)
        for start in range(0, len(target), batch_size)
    ])


def predict_numpy(features, target, weight_path, neighbors=True, batch_size=384):
    """Return fresh raw margins from exported float32 tensors, without PyTorch."""
    if batch_size < 1:
        raise ValueError('GraphSAGE batch size must be positive.')
    if not len(target):
        return np.empty(0, dtype=np.float32)
    weights = _weights(weight_path)
    return np.concatenate([
        _score(features, target[start:start + batch_size], weights, neighbors)
        for start in range(0, len(target), batch_size)
    ])
