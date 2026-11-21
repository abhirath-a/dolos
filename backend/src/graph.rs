use bigdecimal::{BigDecimal, Signed};
use chrono::DateTime;
use serde_json::{Map, Value, json};
use std::collections::{BTreeMap, HashMap, HashSet};

pub fn str_field<'a>(row: &'a Value, key: &str) -> &'a str {
    row[key].as_str().unwrap_or_default()
}

fn fields(row: &Value, keys: &[&str]) -> Value {
    Value::Object(
        keys.iter()
            .map(|&key| (key.to_owned(), row[key].clone()))
            .collect(),
    )
}

fn decimal(value: &Value) -> Option<BigDecimal> {
    value.as_str()?.parse().ok()
}

pub fn graph(rows: &[Value]) -> Value {
    let mut nodes: Vec<Value> = Vec::new();
    let mut ids = HashMap::new();
    let mut edges = Vec::with_capacity(rows.len());
    let mut totals = BTreeMap::<_, (usize, BigDecimal, BigDecimal)>::new();
    let mut relations = BTreeMap::<_, (usize, usize, BigDecimal)>::new();
    for row in rows {
        let (source, target) = (str_field(row, "sender_id"), str_field(row, "receiver_id"));
        let is_relation = row["record_type"] == "relation";
        for (id, side, bank_key, account_key) in [
            (source, "sender", "from_bank", "from_account"),
            (target, "receiver", "to_bank", "to_account"),
        ] {
            let existing = ids.get(id).copied();
            if let Some(index) = existing
                && (!is_relation || !str_field(&nodes[index], "name").is_empty())
            {
                continue;
            }
            let node = if is_relation {
                row[side].clone()
            } else {
                let bank = &row[bank_key];
                let account = &row[account_key];
                json!({"id":id,"bank":bank,"account":account,"label":account,"kind":"account",
                    "identity_basis":"bank_account","resolution_status":"exact_identifier"})
            };
            if let Some(index) = existing {
                nodes[index] = node;
            } else {
                ids.insert(id, nodes.len());
                nodes.push(node);
            }
        }
        let mut edge = if is_relation {
            fields(
                row,
                &[
                    "id",
                    "relation",
                    "timestamp",
                    "timestamp_precision",
                    "amount",
                    "currency",
                    "description",
                    "reference",
                    "evidence",
                    "extraction",
                    "review_required",
                    "review_reasons",
                    "document_id",
                    "source_row",
                ],
            )
        } else {
            let mut edge = fields(
                row,
                &[
                    "id",
                    "timestamp",
                    "amount_paid",
                    "payment_currency",
                    "amount_received",
                    "receiving_currency",
                    "document_id",
                    "source_row",
                ],
            );
            edge["relation"] = "transfer".into();
            if !row["extraction"].is_null() {
                edge["extraction"] = row["extraction"].clone();
                edge["evidence"] = row["evidence"].clone();
            }
            edge
        };
        edge["source"] = source.into();
        edge["target"] = target.into();
        if !is_relation {
            let currencies = (
                str_field(row, "payment_currency"),
                str_field(row, "receiving_currency"),
            );
            let total = totals
                .entry((source, target, currencies.0, currencies.1))
                .or_default();
            total.0 += 1;
            total.1 += decimal(&row["amount_paid"]).unwrap_or_default();
            total.2 += decimal(&row["amount_received"]).unwrap_or_default();
        } else if !row["dataset_id"].is_null() {
            for key in [
                "dataset_id",
                "case_id",
                "case_title",
                "citations",
                "evidence_status",
                "amount_scope",
                "amount_qualifier",
                "date_qualifier",
                "event_period",
            ] {
                edge[key] = row[key].clone();
            }
        } else {
            let currency = row["currency"].as_str();
            let count = relations.entry((source, target, currency)).or_default();
            count.0 += 1;
            if let Some(amount) = currency.and(decimal(&row["amount"])) {
                count.1 += 1;
                count.2 += amount;
            }
        }
        edges.push(edge);
    }
    let summaries: Vec<_> = totals.into_iter().map(|((source,target,payment,receiving),(count,paid,received))|
        json!({"source":source,"target":target,"payment_currency":payment,"receiving_currency":receiving,
            "count":count,"amount_paid":paid.to_string(),"amount_received":received.to_string()})).collect();
    let relation_summaries: Vec<_> = relations.into_iter().map(|((source,target,currency),(count,known,amount))|
        json!({"source":source,"target":target,"currency":currency,"count":count,"amount_known_count":known,
            "amount":(known > 0).then(|| amount.to_string())})).collect();
    json!({"nodes":nodes,"edges":edges,"summaries":summaries,"relation_summaries":relation_summaries})
}

pub fn timestamp(value: &str) -> Option<String> {
    crate::ingest::normalize_time(value).ok()
}

pub fn entities(findings: &[Value]) -> Vec<Value> {
    let mut counts = BTreeMap::<&str, usize>::new();
    for finding in findings {
        for id in HashSet::from([str_field(finding, "source"), str_field(finding, "target")]) {
            *counts.entry(id).or_default() += 1;
        }
    }
    let mut counts: Vec<_> = counts.into_iter().collect();
    counts.sort_by_key(|&(_, count)| std::cmp::Reverse(count));
    counts
        .into_iter()
        .map(|(id, count)| json!({"id":id,"flagged_connections":count}))
        .collect()
}

struct Outflow<'a> {
    id: &'a str,
    time: i64,
    scaled_amount: BigDecimal,
}

fn pass_throughs<'a>(edges: &'a [Value], flags: &mut HashMap<&'a str, Vec<Value>>) {
    const DAY_MICROS: i64 = 86_400_000_000;
    let mut outgoing = HashMap::<(&str, &str), Vec<Outflow>>::new();
    let mut inflows = Vec::with_capacity(edges.len());
    for edge in edges {
        let id = str_field(edge, "id");
        let source = str_field(edge, "source");
        let target = str_field(edge, "target");
        if source == target {
            continue;
        }
        let Ok(time) = DateTime::parse_from_rfc3339(str_field(edge, "timestamp")) else {
            continue;
        };
        let time = time.timestamp_micros();
        let amount = &edge["amount"];
        let currency = &edge["currency"];
        if let (Some(amount), Some(currency)) = (
            decimal(edge.get("amount_paid").unwrap_or(amount)),
            edge.get("payment_currency").unwrap_or(currency).as_str(),
        ) {
            outgoing
                .entry((source, currency))
                .or_default()
                .push(Outflow {
                    id,
                    time,
                    scaled_amount: amount * 10,
                });
        }
        if let (Some(amount), Some(currency)) = (
            decimal(edge.get("amount_received").unwrap_or(amount)),
            edge.get("receiving_currency")
                .unwrap_or(currency)
                .as_str()
                .filter(|value| !value.is_empty()),
        ) && amount.is_positive()
        {
            inflows.push((id, target, currency, time, amount));
        }
    }
    for out in outgoing.values_mut() {
        out.sort_by_key(|edge| edge.time);
    }
    for (id, target, currency, time, amount) in inflows {
        let Some(out) = outgoing.get(&(target, currency)) else {
            continue;
        };
        let start = out.partition_point(|edge| edge.time < time);
        let end = time.saturating_add(DAY_MICROS);
        let (minimum, maximum) = (&amount * 8, &amount * 12);
        if let Some(next) = out[start..]
            .iter()
            .take_while(|edge| edge.time <= end)
            .find(|edge| edge.scaled_amount >= minimum && edge.scaled_amount <= maximum)
        {
            let reason = json!({"rule":"Rapid pass-through","detail":"An incoming and outgoing transfer at this entity occur within 24 hours, in the same currency, with amounts within 20%. This does not establish a funding link.",
                "related_edges":[id,next.id],"entity_id":target});
            flags.entry(id).or_default().push(reason.clone());
            flags.entry(next.id).or_default().push(reason);
        }
    }
}

pub fn analysis(graph: &Value, historical: bool) -> Value {
    let Some(edges) = graph["edges"].as_array() else {
        return Value::Object(Map::new());
    };
    let mut flags = HashMap::<&str, Vec<Value>>::new();
    let pairs = if historical {
        for edge in edges {
            let status = str_field(edge, "evidence_status");
            if ["alleg", "admitted", "conviction", "forfeiture", "court"]
                .iter()
                .any(|part| status.contains(part))
            {
                let detail = edge["description"]
                    .as_str()
                    .filter(|s| !s.is_empty())
                    .map(str::to_owned)
                    .unwrap_or_else(|| status.replace('_', " "));
                flags.entry(str_field(edge, "id")).or_default().push(
                    json!({"rule":"Source-linked case evidence","detail":detail,"basis":status}),
                );
            }
        }
        HashSet::new()
    } else {
        pass_throughs(edges, &mut flags);
        edges
            .iter()
            .map(|edge| (str_field(edge, "source"), str_field(edge, "target")))
            .collect()
    };
    let mut findings = Vec::new();
    for edge in edges {
        let id = str_field(edge, "id");
        let source = str_field(edge, "source");
        let target = str_field(edge, "target");
        let mut reasons = flags.remove(id).unwrap_or_default();
        if source != target && pairs.contains(&(target, source)) {
            reasons.push(json!({"rule":"Reciprocal transfers","detail":"Transfers run in both directions between these entities in this graph. Timing and economic purpose require review."}));
        }
        if !reasons.is_empty() {
            findings.push(json!({"edge_id":id,"source":source,"target":target,"reasons":reasons}));
        }
    }
    json!({"method":if historical {"historical_evidence"} else {"pattern_rules"},
        "rule_scope":"Rapid pass-through shows one matching outgoing transfer per incoming transfer, not an exhaustive flow decomposition.",
        "findings":findings,"entities":entities(&findings),
        "limitation":"Flags identify review candidates, not proof of laundering or responsibility. Unflagged relationships are not certified safe."})
}

#[cfg(test)]
mod tests {
    use super::*;

    fn transfer(id: &str, from: &str, to: &str, date: &str, amount: &str) -> Value {
        json!({"id":id,"sender_id":from,"receiver_id":to,"timestamp":date,
            "from_bank":"001","from_account":from,"to_bank":"002","to_account":to,
            "amount_paid":amount,"amount_received":amount,"payment_currency":"USD","receiving_currency":"USD"})
    }

    #[test]
    fn graph_preserves_decimal_precision_and_uses_exact_pass_through_bounds() {
        let rows = [
            transfer(
                "first",
                "A",
                "B",
                "2025-01-01T00:00:00+00:00",
                "999999999999999999.123456789012345678",
            ),
            transfer(
                "second",
                "B",
                "C",
                "2025-01-02T00:00:00+00:00",
                "999999999999999999.123456789012345678",
            ),
            transfer(
                "late",
                "B",
                "C",
                "2025-01-02T00:00:00.000001+00:00",
                "0.876543210987654322",
            ),
        ];
        let result = graph(&rows);
        assert_eq!(
            result["summaries"][1]["amount_paid"],
            "1000000000000000000.000000000000000000"
        );
        let findings = analysis(&result, false);
        assert_eq!(findings["findings"].as_array().unwrap().len(), 2);
        assert_eq!(
            findings["findings"][0]["reasons"][0]["related_edges"],
            json!(["first", "second"])
        );
    }
}
