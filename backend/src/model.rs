mod features;
mod trees;

use anyhow::{Context, Result, ensure};
use features::{Block, CATEGORIES, Features};
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeSet, HashMap, HashSet},
    fs,
    path::{Component, Path},
};
use tract_onnx::prelude::*;
use trees::Trees;

const BATCH: usize = 384;
const EMBEDDING_WIDTH: usize = 128;
const DETAILS_LIMIT: usize = 10;

type Session = TypedRunnableModel<TypedModel>;

struct Sage {
    layers: [Session; 2],
    head: Session,
    neighbors: bool,
}

struct Classifier {
    session: Session,
    attribution: Trees,
}

#[derive(Deserialize)]
struct Manifest {
    feature_version: String,
    run_id: String,
    source_sha256: String,
    selection_cutoff_us: i64,
    vocabularies: HashMap<String, Vec<String>>,
}

#[derive(Deserialize)]
struct HybridManifest {
    feature_version: String,
    run_id: String,
    base_run_id: String,
    source_sha256: String,
    selection_cutoff_us: i64,
    encoder_sha256: String,
    tree_sha256: String,
    feature_count: usize,
}

#[derive(Deserialize)]
struct ExportManifest {
    version: u32,
    files: HashMap<String, String>,
}

pub struct ModelRuntime {
    manifest: Manifest,
    vocabulary: [Vec<String>; 3],
    hybrid: Option<HybridManifest>,
    sages: HashMap<&'static str, Sage>,
    trees: HashMap<&'static str, Classifier>,
}

impl ModelRuntime {
    pub fn load(directory: &Path) -> Result<Self> {
        let export: ExportManifest = read(directory, "onnx_manifest.json")?;
        ensure!(export.version == 1, "Unsupported ONNX bundle version");
        for (name, expected) in &export.files {
            let path = Path::new(name);
            ensure!(
                path.components().count() == 1
                    && matches!(path.components().next(), Some(Component::Normal(_))),
                "Invalid ONNX bundle file name"
            );
            let hash = format!("{:x}", Sha256::digest(fs::read(directory.join(path))?));
            ensure!(
                &hash == expected,
                "ONNX bundle checksum mismatch for {name}"
            );
        }
        let checked = |name: &str| {
            ensure!(
                export.files.contains_key(name),
                "ONNX bundle is missing {name}"
            );
            Ok(directory.join(name))
        };
        checked("manifest.json")?;
        let manifest: Manifest = read(directory, "manifest.json")?;
        ensure!(
            manifest.feature_version == "hourly-v1",
            "Unsupported model feature version"
        );
        let vocabularies = CATEGORIES
            .iter()
            .map(|name| {
                manifest
                    .vocabularies
                    .get(*name)
                    .cloned()
                    .with_context(|| format!("Missing vocabulary {name}"))
            })
            .collect::<Result<Vec<_>>>()?;
        let vocabulary = vocabularies
            .try_into()
            .map_err(|_| anyhow::anyhow!("Invalid vocabulary count"))?;
        let mut sages = HashMap::new();
        for name in ["graphsage", "graphsage_no_neighbors"] {
            if export.files.contains_key(&format!("{name}_layer0.onnx")) {
                let load =
                    |suffix| -> Result<_> { session(&checked(&format!("{name}_{suffix}.onnx"))?) };
                sages.insert(
                    name,
                    Sage {
                        layers: [load("layer0")?, load("layer1")?],
                        head: load("head")?,
                        neighbors: name == "graphsage",
                    },
                );
            }
        }
        let mut trees = HashMap::new();
        for (name, file) in [
            ("hybrid", "hybrid_xgboost"),
            ("graph_xgboost", "graph_xgboost"),
            ("transaction_xgboost", "transaction_xgboost"),
        ] {
            if export.files.contains_key(&format!("{file}.onnx")) {
                trees.insert(
                    name,
                    Classifier {
                        session: session(&checked(&format!("{file}.onnx"))?)?,
                        attribution: Trees::load(&checked(&format!("{file}.json"))?)?,
                    },
                );
            }
        }
        let hybrid = if let Some(classifier) = trees.get("hybrid") {
            checked("hybrid_manifest.json")?;
            let metadata: HybridManifest = read(directory, "hybrid_manifest.json")?;
            ensure!(
                metadata.feature_version == "sage-embedding-xgb-v1"
                    && metadata.base_run_id == manifest.run_id
                    && metadata.source_sha256 == manifest.source_sha256
                    && metadata.selection_cutoff_us == manifest.selection_cutoff_us
                    && export.files.get("graphsage.npz") == Some(&metadata.encoder_sha256)
                    && export.files.get("hybrid_xgboost.json") == Some(&metadata.tree_sha256)
                    && metadata.feature_count == classifier.attribution.width()
                    && sages.contains_key("graphsage"),
                "Hybrid model provenance or feature schema does not match its artifacts"
            );
            Some(metadata)
        } else {
            None
        };
        ensure!(
            !sages.is_empty() || !trees.is_empty(),
            "The ONNX bundle contains no supported models"
        );
        Ok(Self {
            manifest,
            vocabulary,
            hybrid,
            sages,
            trees,
        })
    }

    pub fn has_model(&self, name: &str) -> bool {
        self.trees.contains_key(name) || self.sages.contains_key(name)
    }

    pub fn score(
        &self,
        rows: &[Value],
        model: &str,
        limit: usize,
        target_ids: Option<&HashSet<String>>,
        explain: bool,
    ) -> Result<Value> {
        ensure!(self.has_model(model), "Missing {model} artifact");
        let run_id = if model == "hybrid" {
            &self
                .hybrid
                .as_ref()
                .context("Missing hybrid metadata")?
                .run_id
        } else {
            &self.manifest.run_id
        };
        let mut result = json!({"model": model, "run_id": run_id, "score_type": "raw_margin", "history_rows": rows.len(), "scores": []});
        if rows.is_empty() {
            return Ok(if model == "hybrid" {
                result
            } else {
                json!({"model": model, "scores": []})
            });
        }
        let features = Features::new(rows, &self.vocabulary)?;
        let mut positions: Vec<_> = (0..rows.len())
            .rev()
            .filter(|&index| {
                features.time(index) >= self.manifest.selection_cutoff_us
                    && target_ids.is_none_or(|targets| {
                        rows[index]["id"]
                            .as_str()
                            .is_some_and(|id| targets.contains(id))
                    })
            })
            .take(limit)
            .collect();
        if positions.is_empty() {
            if model != "hybrid" {
                result = json!({"model": model, "scores": []});
            }
            result["reason"] = json!("No transactions after model selection cutoff.");
            return Ok(result);
        }
        positions.reverse();
        let mut predictions = Vec::with_capacity(positions.len());
        let mut input_rows = Vec::new();
        let tree = self.trees.get(model);
        let sage = self.sages.get(if model == "hybrid" {
            "graphsage"
        } else {
            model
        });
        for positions in positions.chunks(BATCH) {
            let inputs = inputs(&features, positions, model, sage)?;
            let width = inputs.len() / positions.len();
            let prediction = if let Some(tree) = tree {
                ensure!(
                    width == tree.attribution.width(),
                    "Classifier input width does not match model"
                );
                run(&tree.session, [matrix(&inputs, positions.len(), width)?])?
            } else {
                run(
                    &sage.context("Missing GraphSAGE artifact")?.head,
                    [matrix(&inputs, positions.len(), width)?],
                )?
            };
            ensure!(
                prediction.len() == positions.len(),
                "Model output has an invalid shape"
            );
            predictions.extend(prediction);
            if explain && tree.is_some() {
                input_rows.extend(inputs.chunks_exact(width).map(<[f32]>::to_vec));
            }
        }
        let mut order: Vec<_> = (0..positions.len()).collect();
        order.sort_by(|&a, &b| {
            predictions[b].total_cmp(&predictions[a]).then_with(|| {
                rows[positions[a]]["id"]
                    .as_str()
                    .cmp(&rows[positions[b]]["id"].as_str())
            })
        });
        let names = tree
            .map(|tree| self.feature_names(model, tree.attribution.width()))
            .unwrap_or_default();
        let scores = order.into_iter().enumerate().map(|(rank, offset)| {
            let position = positions[offset];
            let mut score = json!({"transaction_id": rows[position]["id"], "score": predictions[offset],
                "context_cutoff_us": features.cutoff(position), "explanation": null});
            if explain {
                let detail = rank < DETAILS_LIMIT;
                if let Some(tree) = tree.filter(|_| detail || model == "hybrid") {
                    let classifier = &tree.attribution;
                    let contributions = classifier.contributions(&input_rows[offset]);
                    let mut credits = json!({
                        "base_margin": classifier.base_margin(),
                        "contribution_sum": contributions.iter().sum::<f64>(),
                    });
                    if model == "hybrid" {
                        credits["component_contributions"] = json!([
                            {"name": "GraphSAGE neighborhood", "contribution": contributions[..EMBEDDING_WIDTH].iter().sum::<f64>()},
                            {"name": "Transaction fields", "contribution": contributions[EMBEDDING_WIDTH..].iter().sum::<f64>()}
                        ]);
                        for field in ["base_margin", "contribution_sum", "component_contributions"] {
                            score[field] = credits[field].clone();
                        }
                    }
                    if detail {
                        let inputs = &input_rows[offset];
                        let mut top: Vec<_> = (0..contributions.len()).collect();
                        top.sort_by(|&a, &b| contributions[b].abs().total_cmp(&contributions[a].abs()));
                        credits["kind"] = json!(if model == "hybrid" { "hybrid" } else { "tree_shap" });
                        credits["tree_count"] = json!(classifier.count());
                        credits["tree_paths"] = json!(classifier.paths(inputs, &names));
                        credits["features"] = json!(top.into_iter().take(8).map(|index| json!({
                            "name": names[index], "value": inputs[index], "contribution": contributions[index],
                        })).collect::<Vec<_>>());
                        if model == "hybrid" {
                            credits["note"] = json!("One XGBoost decision uses GraphSAGE embeddings and transaction fields. Grouped TreeSHAP contributions plus the base margin sum to the raw margin. The contributions attribute influence to model inputs. Historical neighbors show the GraphSAGE context.");
                            append_context(&mut credits, &features, position, rows);
                        } else {
                            credits["note"] = json!("TreeSHAP contributions plus the base margin sum to the raw margin. The table shows the eight largest contributions and their transformed input values. The contributions attribute influence to model inputs.");
                        }
                        score["explanation"] = credits;
                    }
                } else if detail && model == "graphsage" {
                    let mut context = json!({"kind": "graph_context", "note": "Historical neighbors sampled for the two GraphSAGE layers."});
                    append_context(&mut context, &features, position, rows);
                    score["explanation"] = context;
                }
            }
            score
        }).collect::<Vec<_>>();
        result["scores"] = json!(scores);
        Ok(result)
    }

    fn feature_names(&self, model: &str, width: usize) -> Vec<String> {
        let mut names = Vec::new();
        if model == "hybrid" {
            for side in ["sender", "receiver"] {
                names.extend(
                    (1..=EMBEDDING_WIDTH / 2)
                        .map(|index| format!("GraphSAGE {side} embedding {index}")),
                );
            }
        }
        names.extend(["Log received amount".into(), "Log paid amount".into()]);
        for (category, values) in CATEGORIES.into_iter().zip(&self.vocabulary) {
            let category = category.replace('_', " ");
            names.extend(
                std::iter::once("unknown")
                    .chain(values.iter().map(String::as_str))
                    .map(|label| format!("{category}: {label}")),
            );
        }
        if model == "graph_xgboost" {
            for side in ["Sender", "Receiver"] {
                for (direction, currencies) in [
                    ("outgoing", &self.vocabulary[1]),
                    ("incoming", &self.vocabulary[0]),
                ] {
                    names.extend(
                        [
                            "log count",
                            "history present",
                            "log hours since last",
                            "log history span",
                            "log recent count",
                            "log reference rate",
                        ]
                        .map(|measure| format!("{side} {direction} {measure}")),
                    );
                    for measure in [
                        "log count",
                        "signed log total",
                        "signed log mean",
                        "log standard deviation",
                    ] {
                        names.extend(
                            currencies
                                .iter()
                                .map(|code| format!("{side} {direction} {code} {measure}")),
                        );
                    }
                }
            }
        }
        if names.len() == width {
            names
        } else {
            (0..width).map(|index| format!("Feature {index}")).collect()
        }
    }
}

fn read<T: for<'de> Deserialize<'de>>(directory: &Path, name: &str) -> Result<T> {
    serde_json::from_slice(&fs::read(directory.join(name)).with_context(|| format!("Read {name}"))?)
        .with_context(|| format!("Parse {name}"))
}

fn session(path: &Path) -> Result<Session> {
    (|| -> Result<_> {
        tract_onnx::onnx()
            .model_for_path(path)?
            .into_optimized()?
            .into_runnable()
    })()
    .with_context(|| format!("Prepare ONNX model {}", path.display()))
}

fn matrix(values: &[f32], rows: usize, width: usize) -> Result<TValue> {
    Ok(Tensor::from_shape(&[rows, width], values)?.into())
}

fn run(session: &Session, inputs: impl IntoIterator<Item = TValue>) -> Result<Vec<f32>> {
    let output = session.run(inputs.into_iter().collect())?;
    let values = output
        .first()
        .context("ONNX model has no output")?
        .to_array_view::<f32>()?;
    ensure!(
        values.iter().all(|value| value.is_finite()),
        "ONNX model returned a non-finite value"
    );
    Ok(values.iter().copied().collect())
}

fn inputs(
    features: &Features,
    rows: &[usize],
    model: &str,
    sage: Option<&Sage>,
) -> Result<Vec<f32>> {
    if model == "transaction_xgboost" {
        return Ok(rows.iter().flat_map(|&row| features.edge(row)).collect());
    }
    let (roots, endpoints) = features.roots(rows);
    let nodes = if let Some(sage) = sage {
        sage.encode(features, &roots)?
    } else {
        features.nodes(&roots)
    };
    let width = nodes.len() / roots.len();
    let mut values = Vec::with_capacity(rows.len() * (features.edge_width() + 2 * width));
    for (endpoints, &row) in endpoints.as_chunks::<2>().0.iter().zip(rows) {
        let edge = features.edge(row);
        if model == "graph_xgboost" {
            values.extend_from_slice(&edge);
        }
        for &index in endpoints {
            values.extend_from_slice(&nodes[index * width..(index + 1) * width]);
        }
        if model != "graph_xgboost" {
            values.extend(edge);
        }
    }
    Ok(values)
}

impl Sage {
    fn encode(&self, features: &Features, keys: &[features::Key]) -> Result<Vec<f32>> {
        if self.neighbors {
            let inner = features.block(keys, 4);
            let outer = features.block(&inner.keys, 2);
            let nodes = features.nodes(&outer.keys);
            let first = layer(&self.layers[0], &outer, &nodes, features.node_width())?;
            layer(
                &self.layers[1],
                &inner,
                &first,
                first.len() / inner.keys.len(),
            )
        } else {
            let nodes = features.nodes(keys);
            let first = run(
                &self.layers[0],
                [matrix(&nodes, keys.len(), features.node_width())?],
            )?;
            run(
                &self.layers[1],
                [matrix(&first, keys.len(), first.len() / keys.len())?],
            )
        }
    }
}

fn layer(session: &Session, block: &Block, nodes: &[f32], width: usize) -> Result<Vec<f32>> {
    let count = block.roots.len();
    let roots: Vec<_> = block
        .roots
        .iter()
        .flat_map(|&index| &nodes[index * width..(index + 1) * width])
        .copied()
        .collect();
    let mean = |groups: &[Vec<usize>]| {
        let mut values = vec![0.0; count * width];
        for (row, sources) in values.chunks_exact_mut(width).zip(groups) {
            for &source in sources {
                for (target, value) in row
                    .iter_mut()
                    .zip(&nodes[source * width..(source + 1) * width])
                {
                    *target += value;
                }
            }
            if sources.len() > 1 {
                for value in row {
                    *value /= sources.len() as f32;
                }
            }
        }
        values
    };
    run(
        session,
        [
            matrix(&roots, count, width)?,
            matrix(&mean(&block.incoming), count, width)?,
            matrix(&mean(&block.outgoing), count, width)?,
        ],
    )
}

fn append_context(target: &mut Value, features: &Features, row: usize, rows: &[Value]) {
    let (keys, _) = features.roots(&[row]);
    let first = features.block(&keys, 4);
    let second = features.block(&first.keys, 2);
    let first: BTreeSet<_> = first.evidence.into_iter().collect();
    let all: BTreeSet<_> = first.iter().copied().chain(second.evidence).collect();
    target["context_transaction_ids"] = json!(
        all.iter()
            .map(|&index| &rows[index]["id"])
            .collect::<Vec<_>>()
    );
    target["context_transactions"] = json!(
        all.into_iter()
            .map(|index| {
                let mut value = rows[index].clone();
                value["hop"] = json!(if first.contains(&index) { 1 } else { 2 });
                value
            })
            .collect::<Vec<_>>()
    );
    target["cutoff_us"] = json!(features.cutoff(row));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bundled_onnx_matches_recorded_model_margins_and_context() -> Result<()> {
        let directory = Path::new(env!("CARGO_MANIFEST_DIR")).join("../dolos/demo_assets");
        let runtime = ModelRuntime::load(&directory.join("artifacts"))?;
        let demo: Value = read(&directory, "model-demo.json")?;
        let mut source = csv::Reader::from_path(directory.join("synthetic-transfers.csv"))?;
        let mut rows = Vec::new();
        for (index, record) in source.records().enumerate() {
            let record = record?;
            let timestamp =
                chrono::NaiveDateTime::parse_from_str(&record[0], "%Y/%m/%d %H:%M")?.and_utc();
            rows.push(json!({
                "id": format!("simulation:{}", index + 1),
                "timestamp": timestamp.to_rfc3339(),
                "sender_id": json!([&record[1], &record[2]]).to_string(),
                "receiver_id": json!([&record[3], &record[4]]).to_string(),
                "amount_received": &record[5],
                "receiving_currency": &record[6],
                "amount_paid": &record[7],
                "payment_currency": &record[8],
                "payment_format": &record[9],
            }));
        }
        let targets = HashSet::from([demo["focus_edge_id"]
            .as_str()
            .context("Missing demo target")?
            .to_owned()]);
        for reference in demo["models"]
            .as_object()
            .context("Missing model references")?
            .values()
        {
            let name = reference["name"].as_str().context("Missing model name")?;
            let actual = runtime.score(&rows, name, 1, Some(&targets), true)?;
            let actual = &actual["scores"][0];
            let expected = &reference["scores"][0];
            assert_eq!(actual["transaction_id"], expected["transaction_id"]);
            assert_eq!(actual["context_cutoff_us"], expected["context_cutoff_us"]);
            let margin = actual["score"]
                .as_f64()
                .context("Missing evaluated margin")?;
            let reference = expected["score"]
                .as_f64()
                .context("Missing reference margin")?;
            assert!(
                (margin - reference).abs() < 1e-4,
                "{name}: {margin} != {reference}"
            );
            assert_eq!(
                actual["explanation"]["context_transaction_ids"],
                expected["explanation"]["context_transaction_ids"]
            );
            if name == "hybrid" {
                let base = actual["base_margin"]
                    .as_f64()
                    .context("Missing base margin")?;
                let sum = actual["contribution_sum"]
                    .as_f64()
                    .context("Missing contribution sum")?;
                assert!((base + sum - margin).abs() < 1e-4);
                for index in 0..2 {
                    let credit = actual["component_contributions"][index]["contribution"]
                        .as_f64()
                        .context("Missing credit")?;
                    let expected = expected["component_contributions"][index]["contribution"]
                        .as_f64()
                        .context("Missing reference credit")?;
                    assert!(
                        (credit - expected).abs() < 1e-4,
                        "Hybrid credit {index}: {credit} != {expected}"
                    );
                }
            }
        }
        let mixed = demo["examples"]
            .as_array()
            .and_then(|examples| {
                examples
                    .iter()
                    .find(|example| example["id"] == "mixed-network")
            })
            .context("Missing mixed-network example")?;
        let targets: HashSet<_> = mixed["focus_edge_ids"]
            .as_array()
            .context("Missing mixed-network targets")?
            .iter()
            .filter_map(Value::as_str)
            .map(str::to_owned)
            .collect();
        let actual = runtime.score(&rows, "hybrid", targets.len(), Some(&targets), true)?;
        let scores = actual["scores"]
            .as_array()
            .context("Missing mixed-network scores")?;
        assert_eq!(scores.len(), 12);
        let mut positive = 0;
        let mut detailed = 0;
        for item in scores {
            let margin = item["score"]
                .as_f64()
                .context("Missing mixed-network margin")?;
            let base = item["base_margin"]
                .as_f64()
                .context("Missing base margin")?;
            let sum = item["contribution_sum"]
                .as_f64()
                .context("Missing contribution sum")?;
            assert!((base + sum - margin).abs() < 1e-4);
            positive += usize::from(margin > 0.0);
            detailed += usize::from(!item["explanation"].is_null());
        }
        assert_eq!(positive, 4);
        assert_eq!(detailed, 10);
        Ok(())
    }
}
