use anyhow::{Context, Result, ensure};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{fs, path::Path};

#[derive(Deserialize)]
struct Artifact {
    learner: Learner,
}

#[derive(Deserialize)]
struct Learner {
    learner_model_param: Parameters,
    gradient_booster: Booster,
}

#[derive(Deserialize)]
struct Parameters {
    base_score: String,
    num_feature: String,
}

#[derive(Deserialize)]
struct Booster {
    model: Ensemble,
}

#[derive(Deserialize)]
struct Ensemble {
    trees: Vec<RawTree>,
}

#[derive(Deserialize)]
struct RawTree {
    left_children: Vec<i32>,
    right_children: Vec<i32>,
    split_indices: Vec<usize>,
    split_conditions: Vec<f32>,
    default_left: Vec<u8>,
    sum_hessian: Vec<f32>,
}

#[derive(Clone, Copy)]
struct Decision {
    feature: usize,
    threshold: f32,
    lower: bool,
    missing: bool,
}

impl Decision {
    fn includes(&self, value: f32) -> bool {
        if value.is_nan() {
            self.missing
        } else {
            (value < self.threshold) == self.lower
        }
    }
}

struct LeafFeature {
    feature: usize,
    decisions: Vec<Decision>,
    unknown: f64,
}

struct Leaf {
    margin: f32,
    features: Vec<LeafFeature>,
}

struct Tree {
    nodes: RawTree,
    leaves: Vec<Leaf>,
}

pub(super) struct Trees {
    width: usize,
    base_margin: f64,
    trees: Vec<Tree>,
}

impl Trees {
    pub fn load(path: &Path) -> Result<Self> {
        let source: Artifact = serde_json::from_slice(&fs::read(path)?)?;
        let learner = source.learner;
        let width = learner.learner_model_param.num_feature.parse::<usize>()?;
        let base = learner
            .learner_model_param
            .base_score
            .trim_matches(['[', ']'])
            .parse::<f32>()?;
        ensure!(
            base > 0.0 && base < 1.0 && width > 0,
            "Invalid classifier metadata"
        );
        let trees: Vec<_> = learner
            .gradient_booster
            .model
            .trees
            .into_iter()
            .map(|nodes| {
                let count = nodes.left_children.len();
                ensure!(
                    count > 0
                        && [
                            nodes.right_children.len(),
                            nodes.split_indices.len(),
                            nodes.split_conditions.len(),
                            nodes.default_left.len(),
                            nodes.sum_hessian.len(),
                        ]
                        .iter()
                        .all(|&length| length == count),
                    "Invalid classifier tree shape"
                );
                let mut leaves = Vec::new();
                nodes.collect_leaves(0, width, &mut Vec::new(), &mut leaves)?;
                Ok(Tree { nodes, leaves })
            })
            .collect::<Result<_>>()?;
        let base_margin = f64::from(-(1.0 / base - 1.0).ln())
            + trees
                .iter()
                .flat_map(|tree| &tree.leaves)
                .map(|leaf| {
                    f64::from(leaf.margin)
                        * leaf
                            .features
                            .iter()
                            .map(|feature| feature.unknown)
                            .product::<f64>()
                })
                .sum::<f64>();
        Ok(Self {
            width,
            base_margin,
            trees,
        })
    }

    pub fn width(&self) -> usize {
        self.width
    }

    pub fn base_margin(&self) -> f64 {
        self.base_margin
    }

    pub fn count(&self) -> usize {
        self.trees.len()
    }

    pub fn contributions(&self, row: &[f32]) -> Vec<f64> {
        let mut totals = vec![0.0; self.width];
        let mut known = Vec::new();
        let mut coalition = Vec::new();
        for leaf in self.trees.iter().flat_map(|tree| &tree.leaves) {
            known.clear();
            known.extend(leaf.features.iter().map(|feature| {
                if feature
                    .decisions
                    .iter()
                    .all(|split| split.includes(row[feature.feature]))
                {
                    1.0
                } else {
                    0.0
                }
            }));
            for (index, feature) in leaf.features.iter().enumerate() {
                let delta = known[index] - feature.unknown;
                if delta == 0.0 {
                    continue;
                }
                // The Shapley weight averages the product over all coalition sizes.
                // Each coefficient sums path probabilities for one coalition size.
                coalition.clear();
                coalition.resize(leaf.features.len(), 0.0);
                coalition[0] = 1.0;
                let members = leaf
                    .features
                    .iter()
                    .zip(&known)
                    .enumerate()
                    .filter(|(other, _)| *other != index);
                for (count, (_, (other, observed))) in members.enumerate() {
                    for size in (0..=count).rev() {
                        coalition[size + 1] += coalition[size] * observed;
                        coalition[size] *= other.unknown;
                    }
                }
                let depth = coalition.len() - 1;
                let mut combinations = 1.0;
                let mut integrated = 0.0;
                for (size, value) in coalition.iter().enumerate() {
                    integrated += value / combinations;
                    combinations *= (depth - size) as f64 / (size + 1) as f64;
                }
                totals[feature.feature] +=
                    f64::from(leaf.margin) * delta * integrated / coalition.len() as f64;
            }
        }
        totals
    }

    pub fn paths(&self, row: &[f32], names: &[String]) -> Vec<Value> {
        self.trees.iter().take(3).enumerate().map(|(index, tree)| {
            let mut steps = Vec::new();
            let mut node = 0;
            while tree.nodes.left_children[node] >= 0 {
                let feature = tree.nodes.split_indices[node];
                let threshold = tree.nodes.split_conditions[node];
                let value = row[feature];
                let lower = value < threshold;
                steps.push(json!({"feature": names[feature], "value": value, "threshold": threshold,
                    "branch": if lower { "less_than" } else { "greater_or_equal" }}));
                node = if lower { tree.nodes.left_children[node] } else { tree.nodes.right_children[node] } as usize;
            }
            json!({"tree": index + 1, "steps": steps, "leaf_margin": tree.nodes.split_conditions[node]})
        }).collect()
    }
}

impl RawTree {
    fn collect_leaves(
        &self,
        node: usize,
        width: usize,
        path: &mut Vec<(Decision, f64)>,
        leaves: &mut Vec<Leaf>,
    ) -> Result<()> {
        let margin = self.split_conditions[node];
        ensure!(margin.is_finite(), "Invalid classifier value");
        if self.left_children[node] == -1 {
            let mut features: Vec<LeafFeature> = Vec::new();
            for &(decision, weight) in path.iter() {
                if let Some(feature) = features
                    .iter_mut()
                    .find(|item| item.feature == decision.feature)
                {
                    feature.decisions.push(decision);
                    feature.unknown *= weight;
                } else {
                    features.push(LeafFeature {
                        feature: decision.feature,
                        decisions: vec![decision],
                        unknown: weight,
                    });
                }
            }
            leaves.push(Leaf { margin, features });
            return Ok(());
        }
        let feature = self.split_indices[node];
        let parent_weight = f64::from(self.sum_hessian[node]);
        ensure!(
            feature < width && parent_weight.is_finite() && parent_weight > 0.0,
            "Invalid classifier split"
        );
        for (lower, child) in [
            (true, self.left_children[node]),
            (false, self.right_children[node]),
        ] {
            let child = usize::try_from(child).context("Invalid child index")?;
            ensure!(
                child > node && child < self.left_children.len(),
                "Invalid child index"
            );
            let weight = f64::from(self.sum_hessian[child]) / parent_weight;
            ensure!(
                weight.is_finite() && weight >= 0.0,
                "Invalid classifier branch weight"
            );
            path.push((
                Decision {
                    feature,
                    threshold: margin,
                    lower,
                    missing: (self.default_left[node] != 0) == lower,
                },
                weight,
            ));
            self.collect_leaves(child, width, path, leaves)?;
            path.pop();
        }
        Ok(())
    }
}
