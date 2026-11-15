"""Reusable notebook models and benchmark. Importing never trains a model."""
import copy
import csv
import hashlib
import json
import platform
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

import numpy as np
import polars as pl
import torch
from sklearn.metrics import auc, average_precision_score, precision_recall_curve
from torch import nn
from torch_geometric.nn import SAGEConv

CSV_PATH = Path("HI-Small_Trans.csv")
RUN_DIRECTORY = Path("flowgraph_notebook_run")
RUN_FULL_BENCHMARK = False
EPOCHS = 6
BATCH_SIZE = 384

HOUR = 3_600_000_000
FANOUTS = (4, 2)
SEED = 7
BUDGET = 100

IBM_HEADER = (
    "Timestamp", "From Bank", "Account", "To Bank", "Account",
    "Amount Received", "Receiving Currency", "Amount Paid",
    "Payment Currency", "Payment Format", "Is Laundering",
)
COLUMNS = (
    "timestamp", "from_bank", "from_account", "to_bank", "to_account",
    "amount_received", "receiving_currency", "amount_paid",
    "payment_currency", "payment_format", "is_laundering",
)
CATEGORICAL = ("receiving_currency", "payment_currency", "payment_format")
AMOUNTS = ("amount_received", "amount_paid")
TIMESTAMP_FORMAT = "%Y/%m/%d %H:%M"


def read_ibm_csv(path):
    """Assign a zero-based source record ID before filtering or sorting."""
    path = Path(path)
    with path.open(encoding="utf-8-sig", newline="") as source:
        if tuple(next(csv.reader(source), ())) != IBM_HEADER:
            raise ValueError("Expected the original IBM HI-Small transaction header.")
    with path.open("rb") as source:
        digest = hashlib.sha256()
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    frame = pl.read_csv(path, new_columns=COLUMNS, infer_schema=False).with_row_index(
        "transaction_id"
    )
    frame = frame.with_columns(
        pl.col("timestamp").str.strptime(pl.Datetime("us"), TIMESTAMP_FORMAT),
        pl.col(*AMOUNTS).cast(pl.Float64),
        pl.col("is_laundering").cast(pl.UInt8),
        pl.concat_str(["from_bank", "from_account"], separator=":").alias("sender_id"),
        pl.concat_str(["to_bank", "to_account"], separator=":").alias("receiver_id"),
    )
    return frame, digest.hexdigest()


def chronological_splits(times, cutoffs=None):
    """Return positional splits, keeping each timestamp in one partition."""
    if len(times) < 3 or np.any(times[1:] < times[:-1]):
        raise ValueError("Expected at least three chronologically sorted rows.")
    if cutoffs is None:
        cutoffs = times[len(times) * np.array([70, 85]) // 100]
    train_end, val_end = np.searchsorted(times, cutoffs, side="left")
    if not 0 < train_end < val_end < len(times):
        raise ValueError("Cutoffs must yield three nonempty timestamp-disjoint splits.")
    return {
        "train": slice(0, train_end),
        "validation": slice(train_end, val_end),
        "test": slice(val_end, len(times)),
    }


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


def prepare(frame, source_sha256, cutoffs=None):
    """Use raw IBM record IDs, timestamp[us], and bank:account endpoint strings."""
    fields = (
        "transaction_id", "timestamp", "sender_id", "receiver_id",
        "is_laundering", *AMOUNTS, *CATEGORICAL,
    )
    frame = frame.select(fields)
    if frame.schema["timestamp"] != pl.Datetime("us"):
        raise ValueError("timestamp must be timezone-naive Polars Datetime('us').")
    if any(frame.null_count().row(0)):
        raise ValueError("Missing values in required transaction columns.")
    ids = frame["transaction_id"]
    if not ids.dtype.is_integer() or ids.n_unique() != len(frame) or not len(frame) or ids.min() < 0:
        raise ValueError("transaction_id must contain unique nonnegative source record IDs.")
    frame = frame.sort(["timestamp", "transaction_id"])
    ids = frame["transaction_id"].cast(pl.Int64).to_numpy()
    times = frame["timestamp"].cast(pl.Int64).to_numpy()
    splits = chronological_splits(times, cutoffs)
    labels = frame["is_laundering"].to_numpy()
    if not np.isin(labels, [0, 1]).all():
        raise ValueError("Expected binary laundering labels.")
    labels = labels.astype(np.uint8)
    amounts = frame.select(AMOUNTS).to_numpy().astype(np.float32)
    if not np.isfinite(amounts).all():
        raise ValueError("Expected finite amounts.")
    amounts = np.sign(amounts) * np.log1p(np.abs(amounts))

    endpoints = pl.concat([frame["sender_id"], frame["receiver_id"]]).cast(pl.Categorical)
    sender, receiver = np.split(endpoints.to_physical().cast(pl.Int32).to_numpy(), 2)
    account_count = int(max(sender.max(), receiver.max())) + 1
    vocabularies, codes = {}, []
    for name in CATEGORICAL:
        values = frame[name]
        vocab = values[:splits["train"].stop].unique().sort().to_list()
        vocabularies[name] = vocab
        mapping = {value: index + 1 for index, value in enumerate(vocab)}
        codes.append(values.replace_strict(mapping, default=0, return_dtype=pl.Int32).to_numpy())

    return Transactions(
        ids, times, sender, receiver, labels, amounts, np.column_stack(codes),
        vocabularies, splits,
        History.build(sender, receiver, times, account_count),
        History.build(receiver, sender, times, account_count),
        source_sha256,
    )

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

def batches(rows, size):
    for start in range(0, len(rows), size):
        yield rows[start:start + size]



def metrics(ids, labels, scores):
    if not np.isfinite(scores).all():
        raise ValueError("Expected finite ranking scores.")
    ranked = labels[np.lexsort((ids, -scores))]
    positives = int(labels.sum())
    if positives:
        precision, recall, _ = precision_recall_curve(labels, scores)
    return {
        "rows": len(labels), "positives": positives, "prevalence": float(labels.mean()),
        "average_precision": float(average_precision_score(labels, scores)) if positives else None,
        "pr_auc_trapezoid": float(auc(recall, precision)) if positives else None,
        **{f"precision_at_{k}": float(ranked[:k].mean()) if len(ranked) >= k else None
           for k in (50, 100, 500, 1000)},
    }

class DirectedSAGE(nn.Module):
    def __init__(self, node_dim, edge_dim, neighbors=True):
        super().__init__()
        self.neighbors = neighbors
        self.root = nn.ModuleList([nn.Linear(node_dim, 64), nn.Linear(64, 64)])
        self.incoming = nn.ModuleList([SAGEConv((d,d), 64, root_weight=False, bias=False)
                                      for d in (node_dim,64)])
        self.outgoing = nn.ModuleList([SAGEConv((d,d), 64, root_weight=False, bias=False)
                                      for d in (node_dim,64)])
        self.head = nn.Sequential(nn.Linear(128+edge_dim,64), nn.ReLU(),
                                  nn.Dropout(.1), nn.Linear(64,1))

    def score(self, features, rows):
        device = self.head[0].weight.device
        def tensor(values):
            return torch.as_tensor(values, device=device)
        roots, endpoints = features.roots(rows)
        attrs = tensor(features.data.edge_features(rows))
        if self.neighbors:
            middle, inner = features.block(roots, FANOUTS[0])
            outer_keys, outer = features.block(middle, FANOUTS[1])
            x = tensor(features.nodes(outer_keys))
            for layer, (target, edges, _) in enumerate((outer,inner)):
                root = x[tensor(target)]
                x = (self.root[layer](root) + self.incoming[layer]((x,root),tensor(edges[0]))
                     + self.outgoing[layer]((x,root),tensor(edges[1]))).relu()
        else:
            x = tensor(features.nodes(roots))
            for layer in self.root:
                x = layer(x).relu()
        return self.head(torch.cat((x[tensor(endpoints)].reshape(len(rows),-1),attrs),1)).flatten()


@torch.inference_mode()
def predict(model, features, rows, batch_size):
    model.eval()
    return np.concatenate([model.score(features, chunk).cpu().numpy()
                           for chunk in batches(rows, batch_size)])


def fit_sage(features, train, validation, *, neighbors, epochs, batch_size):
    labels = features.data.labels
    positive, negative = train[labels[train]==1], train[labels[train]==0]
    rng = np.random.default_rng(SEED)
    torch.manual_seed(SEED)
    model = DirectedSAGE(features.node_dim, features.data.edge_features(train[:1]).shape[1],
                         neighbors)
    optimizer = torch.optim.AdamW(model.parameters(), lr=.0015)
    best, state, chosen = -1, None, None
    for epoch in range(1,epochs+1):
        model.train()
        for pos in batches(rng.permutation(positive), max(1,batch_size//64)):
            neg = rng.choice(negative, min(len(negative),batch_size-len(pos)), replace=False)
            scores = model.score(features, np.r_[pos,neg])
            loss = .5*(nn.functional.softplus(-scores[:len(pos)]).mean()
                       + nn.functional.softplus(scores[len(pos):]).mean())
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
        val = predict(model, features, validation, batch_size)
        ap = average_precision_score(labels[validation],val)
        print(f"sage neighbors={neighbors} epoch={epoch} validation AP={ap:.6f}",flush=True)
        if ap > best:
            best, state, chosen = ap, copy.deepcopy(model.state_dict()), epoch
    model.load_state_dict(state)
    return model, chosen


def rule_scores(data, train, test):
    scores = np.zeros(len(test),dtype=np.float32)
    for currency in np.unique(data.categories[train,1]):
        sorted_amounts = np.sort(data.amounts[train[data.categories[train,1]==currency],1])
        mask = data.categories[test,1]==currency
        scores[mask] = np.searchsorted(sorted_amounts,data.amounts[test[mask],1],side="right") / len(sorted_amounts)
    return scores

def run(csv, directory, epochs=6, batch_size=384, scope_rows=None):
    from xgboost import XGBClassifier

    if epochs < 1 or batch_size < 2 or (scope_rows is not None and scope_rows < 3):
        raise ValueError("Expected positive epochs, batch size ≥2, and at least three scope rows.")

    started = perf_counter()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    raw, fingerprint = read_ibm_csv(csv)
    source_rows = len(raw)
    sorted_times = raw["timestamp"].cast(pl.Int64).sort().to_numpy()
    scope_end = None
    if scope_rows is not None and scope_rows < source_rows:
        scope_end = int(sorted_times[scope_rows] // HOUR * HOUR)
        raw = raw.filter(pl.col("timestamp").cast(pl.Int64) < scope_end)
        sorted_times = sorted_times[:len(raw)]
    bounds = sorted_times[np.array([7*len(raw)//10,85*len(raw)//100])] // HOUR * HOUR
    data = prepare(raw, fingerprint, bounds)
    del raw
    features = SnapshotFeatures(data)
    train, validation, test = (np.arange(s.start,s.stop) for s in data.splits.values())
    torch.set_num_threads(4)
    labels, results, choices = data.labels, {}, {}
    if any(np.unique(labels[rows]).size < 2 for rows in (train,validation,test)):
        raise ValueError("Every partition must contain both classes for this experiment.")
    run_id = f"{fingerprint[:12]}-hourly-v1-{len(data.ids)}-e{epochs}"
    pl.DataFrame({"transaction_id":data.ids[test],"label":labels[test]}).write_parquet(
        directory/"test_labels.parquet")
    root = np.column_stack((data.sender[test],data.receiver[test]))
    unseen = np.zeros(len(test),dtype=np.uint8)
    for accounts in root.T:
        unseen += sum(h.before(accounts, features.cutoffs[test])[1]-h.offsets[accounts]
                      for h in (data.incoming,data.outgoing)) == 0

    def save(name, scores):
        ids = data.ids[test]
        report = metrics(ids,labels[test],scores)
        ranked = np.lexsort((ids,-scores))[:BUDGET]
        tp = int(labels[test][ranked].sum())
        positives = int(labels[test].sum())
        report["recall_at_100"] = tp/positives if positives else None
        report["confusion_at_100"] = dict(tp=tp,fp=len(ranked)-tp,fn=positives-tp,
                                        tn=len(test)-len(ranked)-positives+tp)
        report["coverage"] = 1.0
        report["history_subgroups"] = {
            str(i): metrics(ids[unseen==i],labels[test][unseen==i],scores[unseen==i])
            for i in range(3) if np.any(unseen==i)
        }
        days = (features.cutoffs[test]-bounds[1])//(24*HOUR)
        report["daily"] = {str(i): metrics(ids[days==i],labels[test][days==i],scores[days==i])
                           for i in np.unique(days)}
        results[name] = report
        pl.DataFrame({"transaction_id":ids,"target_time":data.times[test],
                      "context_cutoff":features.cutoffs[test],"score":scores,
                      "snapshot_id":features.cutoffs[test],"unseen_endpoints":unseen,
                      "model_id":[name]*len(test),"status":["available"]*len(test),
                      "run_id":[run_id]*len(test),
                      "rank_scope":["test"]*len(test)}).write_parquet(directory/f"{name}.parquet")
        print(name,report["average_precision"],report["precision_at_100"],flush=True)

    save("currency_rule",rule_scores(data,train,test))
    for historical in (False,True):
        name = "graph_xgboost" if historical else "transaction_xgboost"
        def matrix(rows):
            target = data.edge_features(rows)
            if not historical:
                return target
            roots, endpoints = features.roots(rows)
            return np.column_stack((target,features.nodes(roots)[endpoints].reshape(len(rows),-1)))
        width = matrix(train[:1]).shape[1]
        inputs = np.empty((len(train),width),dtype=np.float32)
        for chunk in batches(train,32_768):
            inputs[chunk] = matrix(chunk)
        model = XGBClassifier(n_estimators=150,max_depth=5,learning_rate=.08,n_jobs=4,
                              random_state=SEED,tree_method="hist")
        model.fit(inputs,labels[train])
        del inputs
        model.save_model(directory/f"{name}.json")
        save(name,np.concatenate([model.predict(matrix(chunk),output_margin=True)
                                  for chunk in batches(test,32_768)]))
    for neighbors in (True,False):
        name = "graphsage" if neighbors else "graphsage_no_neighbors"
        model, epoch = fit_sage(features,train,validation,neighbors=neighbors,
                                epochs=epochs,batch_size=batch_size)
        choices[name] = epoch
        torch.save(model.state_dict(),directory/f"{name}.pt")
        save(name,predict(model,features,test,batch_size))
    write_snapshot_index(data.times, data.ids, features.cutoffs[test], directory)
    manifest = dict(runtime={name: version(name) for name in
                             ("numpy", "polars", "torch", "torch-geometric", "scikit-learn", "xgboost")},
                    hardware=platform.machine(), device="cpu", threads=4,
                    wall_seconds=round(perf_counter() - started, 2),
                    run_id=run_id,source_rows=source_rows,scope_end_us=scope_end,source_sha256=fingerprint,feature_version="hourly-v1",seed=SEED,
                    time_basis="synthetic naive epoch microseconds",bucket_us=HOUR,
                    validation_start_us=int(bounds[0]),test_start_us=int(bounds[1]),
                    training_cutoff_us=int(bounds[0]),selection_cutoff_us=int(bounds[1]),
                    vocabularies=data.vocabularies,
                    graph_policy="Both hops use the same cutoff. Directional means. Repeats contribute multiple messages, including self-edges in both roles.",
                    fanouts_per_direction=FANOUTS,currency_policy="Training vocabulary, per-currency amount history",
                    review_budget=BUDGET,epochs=epochs,selected_epochs=choices,
                    splits={k:dict(rows=int(s.stop-s.start),positives=int(labels[s].sum()),
                                   start_us=int(data.times[s.start]),end_us=int(data.times[s.stop-1]))
                            for k,s in data.splits.items()},metrics=results,
                    artifact_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in directory.iterdir() if p.suffix in (".pt",".json")})
    (directory/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")


def write_snapshot_index(times, ids, cutoffs, directory):
    """Fingerprint the historical source rows behind every scored snapshot."""
    boundaries = np.unique(cutoffs)
    ends = np.searchsorted(times, boundaries, side="left")
    digest = hashlib.sha256()
    previous = 0
    records = []
    for boundary, end in zip(boundaries, ends):
        digest.update(np.asarray(ids[previous:end], dtype="<i8").tobytes())
        previous = end
        records.append({
            "snapshot_id": int(boundary), "context_cutoff": int(boundary),
            "history_start": int(times[0]), "history_rows": int(end),
            "bucket_us": HOUR, "feature_version": "hourly-v1",
            "graph_row_id_sha256": digest.hexdigest(),
            "currency_policy": "training vocabulary; per-currency summaries",
        })
    pl.DataFrame(records).write_parquet(Path(directory) / "snapshot_index.parquet")
