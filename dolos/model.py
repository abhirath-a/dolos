"""Reusable notebook models and benchmark. Importing never trains a model."""
import copy
import csv
import hashlib
import json
import platform
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

import numpy as np
import polars as pl
import torch
from sklearn.metrics import auc, average_precision_score, precision_recall_curve
from torch import nn
from torch_geometric.nn import SAGEConv

from .features import History, Transactions, SnapshotFeatures, unique_keys, HOUR, FANOUTS, CATEGORICAL


def package_version(name):
    """CPU-only XGBoost publishes the same runtime under a distinct distribution."""
    try:
        return version(name)
    except PackageNotFoundError:
        if name == 'xgboost':
            return version('xgboost-cpu')
        raise

CSV_PATH = Path("HI-Small_Trans.csv")
RUN_DIRECTORY = Path("flowgraph_notebook_run")
RUN_FULL_BENCHMARK = False
EPOCHS = 6
BATCH_SIZE = 384

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
        state = model.state_dict()
        torch.save(state,directory/f"{name}.pt")
        np.savez_compressed(directory/f"{name}.npz",
                            **{key: value.detach().cpu().numpy() for key, value in state.items()})
        save(name,predict(model,features,test,batch_size))
    write_snapshot_index(data.times, data.ids, features.cutoffs[test], directory)
    manifest = dict(runtime={name: package_version(name) for name in
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
                                     for p in directory.iterdir() if p.suffix in (".pt",".npz",".json")})
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
