"""Shared, NumPy-only transaction history and hourly inference features.

Training and serving import the same implementations so temporal sampling,
frozen vocabulary handling, and feature ordering remain identical.
"""
from dataclasses import dataclass
import numpy as np

HOUR = 3_600_000_000
FANOUTS = (4, 2)
CATEGORICAL = ("receiving_currency", "payment_currency", "payment_format")

@dataclass
class History:
    offsets: np.ndarray
    times: np.ndarray
    neighbors: np.ndarray
    rows: np.ndarray

    @classmethod
    def build(cls, center, neighbor, times, account_count):
        order = np.argsort(center, kind="stable")
        counts = np.bincount(center, minlength=account_count)
        offsets = np.r_[0, counts.cumsum()]
        return cls(offsets, times[order], neighbor[order], order.astype(np.int32))

    def before(self, accounts, cutoffs):
        """Vectorized lower bound inside each account's sorted adjacency list."""
        start = self.offsets[accounts]
        low, high = start.copy(), self.offsets[accounts + 1].copy()
        while np.any(low < high):
            active = low < high
            middle = (low + high) // 2
            earlier = self.times[np.minimum(middle, len(self.times) - 1)] < cutoffs
            low = np.where(active & earlier, middle + 1, low)
            high = np.where(active & ~earlier, middle, high)
        return start, low

    def recent(self, accounts, cutoffs, fanout):
        start, stop = self.before(accounts, cutoffs)
        positions = stop[:, None] - 1 - np.arange(fanout)
        target, rank = np.nonzero(positions >= start[:, None])
        return target, positions[target, rank]


@dataclass
class Transactions:
    ids: np.ndarray
    times: np.ndarray
    sender: np.ndarray
    receiver: np.ndarray
    labels: np.ndarray
    amounts: np.ndarray
    categories: np.ndarray
    vocabularies: dict
    splits: dict
    outgoing: History
    incoming: History
    source_sha256: str
    historical: np.ndarray = None
    history_profile: str = None

    def edge_features(self, rows):
        parts = [self.amounts[rows]]
        for column, name in enumerate(CATEGORICAL):
            codes = self.categories[rows, column]
            one_hot = np.zeros((len(rows), len(self.vocabularies[name]) + 1), np.float32)
            one_hot[np.arange(len(rows)), codes] = 1
            parts.append(one_hot)
        if self.historical is not None:
            parts.append(self.historical[rows])
        return np.concatenate(parts, axis=1)

    def node_features(self, keys):
        accounts, cutoffs = keys.T
        parts = []
        for history in (self.outgoing, self.incoming):
            start, stop = history.before(accounts, cutoffs)
            count = stop - start
            age_hours = np.zeros(len(keys), dtype=np.float32)
            seen = count > 0
            age_hours[seen] = (
                cutoffs[seen] - history.times[stop[seen] - 1]
            ) / 3_600_000_000
            parts.extend((np.log1p(count), np.log1p(age_hours)))
        return np.column_stack(parts).astype(np.float32)



def unique_keys(keys):
    accounts = keys[:, 0]
    times = keys[:, 1]
    base = int(times.min())
    span = int(times.max()) - base
    if span >= 2**32:
        times_unique, time_codes = np.unique(times, return_inverse=True)
        packed = (accounts << 32) | time_codes
        unique, inverse = np.unique(packed, return_inverse=True)
        return np.column_stack((unique >> 32, times_unique[unique & 0xffffffff])), inverse
    packed = (accounts << 32) | (times - base)
    unique, inverse = np.unique(packed, return_inverse=True)
    return np.column_stack((unique >> 32, (unique & 0xffffffff) + base)), inverse



class SnapshotFeatures:
    def __init__(self, data):
        self.data = data
        self.cutoffs = data.times // HOUR * HOUR
        self.accounts = len(data.incoming.offsets) - 1
        self.currency_history = []
        for history, currency, amount in ((data.outgoing, 1, 1), (data.incoming, 0, 0)):
            vocab = len(data.vocabularies[("receiving_currency", "payment_currency")[currency]])
            codes = data.categories[history.rows, currency]
            raw = data.amounts[history.rows, amount].astype(np.float64)
            raw = np.sign(raw) * np.expm1(np.abs(raw))
            before = np.repeat(np.arange(self.accounts, dtype=np.int32), np.diff(history.offsets))
            order = np.lexsort((history.times, before.astype(np.int64) * (vocab + 1) + codes))
            groups = before[order].astype(np.int64) * (vocab + 1) + codes[order]
            boundaries = np.searchsorted(groups, np.arange(self.accounts * (vocab + 1) + 1))
            self.currency_history.append((
                vocab, boundaries, history.times[order],
                np.r_[0., np.cumsum(raw[order])], np.r_[0., np.cumsum(raw[order] ** 2)],
            ))

    @property
    def node_dim(self):
        return 12 + sum(4 * item[0] for item in self.currency_history)

    def nodes(self, keys):
        account, cutoff = keys.T
        features = []
        for history, item in zip((self.data.outgoing, self.data.incoming), self.currency_history):
            start, stop = history.before(account, cutoff)
            _, recent = history.before(account, cutoff - HOUR)
            _, reference = history.before(account, cutoff - 7 * HOUR)
            count = stop - start
            seen = count > 0
            last = history.times[np.maximum(stop - 1, 0)]
            first = history.times[np.minimum(start, len(history.times) - 1)]
            features.extend((
                np.log1p(count), seen, np.log1p(np.where(seen, (cutoff-last)/HOUR, 0)),
                np.log1p(np.where(seen, (cutoff-first)/HOUR, 0)),
                np.log1p(stop-recent), np.log1p((recent-reference)/6),
            ))
            vocab, bounds, times, sums, squares = item
            groups = account[:, None] * (vocab + 1) + np.arange(1, vocab + 1)
            lo = bounds[groups]
            left, right = lo.copy(), bounds[groups+1].copy()
            while np.any(left < right):
                active = left < right
                middle = (left + right) // 2
                earlier = times[np.minimum(middle, len(times)-1)] < cutoff[:, None]
                left = np.where(active & earlier, middle+1, left)
                right = np.where(active & ~earlier, middle, right)
            n = left-lo
            total = np.where(n, sums[left]-sums[lo], 0)
            mean = total / np.maximum(n, 1)
            variance = np.maximum((squares[left]-squares[lo])/np.maximum(n, 1)-mean**2, 0)
            features.extend((np.log1p(n), np.sign(total)*np.log1p(abs(total)),
                             np.sign(mean)*np.log1p(abs(mean)), np.log1p(np.sqrt(variance))))
        return np.concatenate([np.reshape(x, (len(keys), -1)) for x in features], axis=1).astype(np.float32) * .2

    def roots(self, rows):
        data = self.data
        accounts = np.column_stack((data.sender[rows], data.receiver[rows])).reshape(-1)
        return unique_keys(np.column_stack((accounts, np.repeat(self.cutoffs[rows], 2))))

    def block(self, targets, fanout):
        sources = [targets]
        destinations = []
        evidence = []
        for history in (self.data.incoming, self.data.outgoing):
            dest, pos = history.recent(targets[:, 0], targets[:, 1], fanout)
            sources.append(np.column_stack((history.neighbors[pos], targets[dest, 1])))
            destinations.append(dest)
            evidence.append(history.rows[pos])
        keys, inverse = unique_keys(np.concatenate(sources))
        root = inverse[:len(targets)]
        split = len(targets) + len(destinations[0])
        edges = (np.stack((inverse[len(targets):split], destinations[0])),
                 np.stack((inverse[split:], destinations[1])))
        return keys, (root, edges, evidence)

