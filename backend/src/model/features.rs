use anyhow::{Context, Result, bail};
use chrono::{DateTime, NaiveDateTime};
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet, HashMap},
    ops::Range,
};

pub(super) const CATEGORIES: [&str; 3] =
    ["receiving_currency", "payment_currency", "payment_format"];
const HOUR: i64 = 3_600_000_000;

pub(super) type Key = (usize, i64);

struct Transfer {
    time: i64,
    sender: usize,
    receiver: usize,
    amounts: [f32; 2],
    codes: [usize; 3],
}

#[derive(Clone, Copy)]
struct Neighbor {
    time: i64,
    account: usize,
    row: usize,
}

struct Direction {
    history: Vec<Vec<Neighbor>>,
    currencies: HashMap<(usize, usize), Range<usize>>,
    times: Vec<i64>,
    vocab: usize,
    sums: Vec<f64>,
    squares: Vec<f64>,
}

pub(super) struct Features {
    transfers: Vec<Transfer>,
    incoming: Direction,
    outgoing: Direction,
    counts: [usize; 3],
}

pub(super) struct Block {
    pub keys: Vec<Key>,
    pub roots: Vec<usize>,
    pub incoming: Vec<Vec<usize>>,
    pub outgoing: Vec<Vec<usize>>,
    pub evidence: Vec<usize>,
}

fn timestamp(row: &Value) -> Result<i64> {
    let text = row["timestamp"]
        .as_str()
        .context("Missing transaction timestamp")?;
    let text = text.replace(' ', "T");
    let utc = match DateTime::parse_from_rfc3339(&text) {
        Ok(value) => value.to_utc(),
        Err(_) => NaiveDateTime::parse_from_str(&text, "%Y-%m-%dT%H:%M:%S%.f")
            .or_else(|_| NaiveDateTime::parse_from_str(&text, "%Y-%m-%dT%H:%M"))?
            .and_utc(),
    };
    Ok(utc.timestamp_micros())
}

fn amount(row: &Value, key: &str) -> Result<f32> {
    let value = row[key]
        .as_f64()
        .or_else(|| row[key].as_str()?.parse().ok())
        .context("Invalid transaction amount")? as f32;
    if !value.is_finite() {
        bail!("Transaction amount exceeds model precision");
    }
    Ok(value.abs().ln_1p().copysign(value))
}

impl Features {
    pub fn new(rows: &[Value], vocabulary: &[Vec<String>; 3]) -> Result<Self> {
        let accounts: BTreeSet<_> = rows
            .iter()
            .flat_map(|row| [row["sender_id"].as_str(), row["receiver_id"].as_str()])
            .collect::<Option<_>>()
            .context("Missing transaction account")?;
        let accounts: HashMap<_, _> = accounts
            .into_iter()
            .enumerate()
            .map(|(code, id)| (id, code))
            .collect();
        let codes = vocabulary.each_ref().map(|values| {
            values
                .iter()
                .enumerate()
                .map(|(index, value)| (value.as_str(), index + 1))
                .collect::<HashMap<_, _>>()
        });
        let mut transfers = Vec::with_capacity(rows.len());
        for row in rows {
            let time = timestamp(row)?;
            if transfers
                .last()
                .is_some_and(|last: &Transfer| last.time > time)
            {
                bail!("Inference requires chronological history");
            }
            transfers.push(Transfer {
                time,
                sender: accounts[row["sender_id"].as_str().context("Missing sender")?],
                receiver: accounts[row["receiver_id"].as_str().context("Missing receiver")?],
                amounts: [amount(row, "amount_received")?, amount(row, "amount_paid")?],
                codes: std::array::from_fn(|index| {
                    row[CATEGORIES[index]]
                        .as_str()
                        .and_then(|label| codes[index].get(label).copied())
                        .unwrap_or(0)
                }),
            });
        }
        let counts = vocabulary.each_ref().map(|values| values.len());
        Ok(Self {
            outgoing: Direction::build(&transfers, accounts.len(), counts[1], false),
            incoming: Direction::build(&transfers, accounts.len(), counts[0], true),
            transfers,
            counts,
        })
    }

    pub fn time(&self, row: usize) -> i64 {
        self.transfers[row].time
    }

    pub fn cutoff(&self, row: usize) -> i64 {
        self.time(row).div_euclid(HOUR) * HOUR
    }

    pub fn edge_width(&self) -> usize {
        2 + self.counts.iter().sum::<usize>() + self.counts.len()
    }

    pub fn node_width(&self) -> usize {
        12 + 4 * (self.counts[0] + self.counts[1])
    }

    pub fn edge(&self, row: usize) -> Vec<f32> {
        let transfer = &self.transfers[row];
        let mut values = Vec::with_capacity(self.edge_width());
        values.extend(transfer.amounts);
        for (width, code) in self.counts.into_iter().zip(transfer.codes) {
            let start = values.len();
            values.resize(start + width + 1, 0.0);
            values[start + code] = 1.0;
        }
        values
    }

    pub fn nodes(&self, keys: &[Key]) -> Vec<f32> {
        let mut values = Vec::with_capacity(keys.len() * self.node_width());
        for &key in keys {
            self.outgoing.append(key, &mut values);
            self.incoming.append(key, &mut values);
        }
        values
    }

    pub fn roots(&self, rows: &[usize]) -> (Vec<Key>, Vec<usize>) {
        let pairs: Vec<_> = rows
            .iter()
            .flat_map(|&row| {
                let transfer = &self.transfers[row];
                let cutoff = self.cutoff(row);
                [(transfer.sender, cutoff), (transfer.receiver, cutoff)]
            })
            .collect();
        unique(&pairs)
    }

    pub fn block(&self, targets: &[Key], fanout: usize) -> Block {
        let mut all_keys = targets.to_vec();
        let mut evidence = Vec::new();
        let mut directions = Vec::new();
        for direction in [&self.incoming, &self.outgoing] {
            let mut starts = Vec::with_capacity(targets.len() + 1);
            for &(account, cutoff) in targets {
                starts.push(all_keys.len());
                let neighbors = &direction.history[account];
                let end = neighbors.partition_point(|item| item.time < cutoff);
                for neighbor in neighbors[..end].iter().rev().take(fanout) {
                    all_keys.push((neighbor.account, cutoff));
                    evidence.push(neighbor.row);
                }
            }
            starts.push(all_keys.len());
            directions.push(starts);
        }
        let (keys, inverse) = unique(&all_keys);
        let groups = |starts: &[usize]| {
            starts
                .windows(2)
                .map(|pair| inverse[pair[0]..pair[1]].to_vec())
                .collect()
        };
        Block {
            keys,
            roots: inverse[..targets.len()].to_vec(),
            incoming: groups(&directions[0]),
            outgoing: groups(&directions[1]),
            evidence,
        }
    }
}

fn unique(values: &[Key]) -> (Vec<Key>, Vec<usize>) {
    let keys: Vec<_> = values
        .iter()
        .copied()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    let indices: HashMap<_, _> = keys
        .iter()
        .enumerate()
        .map(|(index, &key)| (key, index))
        .collect();
    (keys, values.iter().map(|key| indices[key]).collect())
}

impl Direction {
    fn build(rows: &[Transfer], accounts: usize, vocab: usize, incoming: bool) -> Self {
        let mut history = vec![Vec::new(); accounts];
        let mut groups = BTreeMap::<(usize, usize), Vec<(i64, f64)>>::new();
        let field = usize::from(!incoming);
        for (row, transfer) in rows.iter().enumerate() {
            let (center, account) = if incoming {
                (transfer.receiver, transfer.sender)
            } else {
                (transfer.sender, transfer.receiver)
            };
            let time = transfer.time;
            history[center].push(Neighbor { time, account, row });
            let value = f64::from(transfer.amounts[field]);
            groups
                .entry((center, transfer.codes[field]))
                .or_default()
                .push((time, value.abs().exp_m1().copysign(value)));
        }
        let mut times = Vec::with_capacity(rows.len());
        let mut sums = vec![0.0];
        let mut squares = vec![0.0];
        let (mut sum, mut square_sum) = (0.0, 0.0);
        let currencies = groups
            .into_iter()
            .map(|(key, entries)| {
                let start = times.len();
                for (time, value) in entries {
                    times.push(time);
                    sum += value;
                    square_sum += value * value;
                    sums.push(sum);
                    squares.push(square_sum);
                }
                (key, start..times.len())
            })
            .collect();
        Self {
            history,
            currencies,
            times,
            vocab,
            sums,
            squares,
        }
    }

    fn append(&self, (account, cutoff): Key, result: &mut Vec<f32>) {
        let history = &self.history[account];
        let before = |value| history.partition_point(|item| item.time < value);
        let count = before(cutoff);
        let recent = before(cutoff - HOUR);
        let reference = before(cutoff - 7 * HOUR);
        let age = |index: usize| ((cutoff - history[index].time) as f64 / HOUR as f64).ln_1p();
        let scale = |value| (value as f32) * 0.2;
        result.extend(
            [
                (count as f64).ln_1p(),
                if count > 0 { 1.0 } else { 0.0 },
                if count > 0 { age(count - 1) } else { 0.0 },
                if count > 0 { age(0) } else { 0.0 },
                ((count - recent) as f64).ln_1p(),
                ((recent - reference) as f64 / 6.0).ln_1p(),
            ]
            .map(scale),
        );
        let start = result.len();
        result.resize(start + self.vocab * 4, 0.0);
        for code in 1..=self.vocab {
            let Some(range) = self.currencies.get(&(account, code)) else {
                continue;
            };
            let count = self.times[range.clone()].partition_point(|&time| time < cutoff);
            let total = self.sums[range.start + count] - self.sums[range.start];
            let mean = total / count.max(1) as f64;
            let quadratic = self.squares[range.start + count] - self.squares[range.start];
            let variance = (quadratic / count.max(1) as f64 - mean * mean).max(0.0);
            for (measure, value) in [
                (count as f64).ln_1p(),
                total.abs().ln_1p().copysign(total),
                mean.abs().ln_1p().copysign(mean),
                variance.sqrt().ln_1p(),
            ]
            .into_iter()
            .enumerate()
            {
                result[start + measure * self.vocab + code - 1] = scale(value);
            }
        }
    }
}
