//! Deterministic validation and relation projection for extracted PDF observations.
use regex::Regex;
use serde::Deserialize;
use serde_json::{Value, json};
use std::sync::LazyLock;
use unicode_casefold::UnicodeCaseFold;
use unicode_normalization::UnicodeNormalization;

use super::{Provider, hash};
use crate::ingest::core::{ascii_json, decimal, normalize_time};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Extraction {
    pub(super) transfers: Vec<Transfer>,
    pub(super) warnings: Vec<String>,
    pub(super) complete: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Party {
    name: Option<String>,
    bank: Option<String>,
    account: Option<String>,
    kind: PartyKind,
}

#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
enum PartyKind {
    Person,
    Organization,
    Unknown,
}

impl PartyKind {
    fn as_str(&self) -> &'static str {
        match self {
            Self::Person => "person",
            Self::Organization => "organization",
            Self::Unknown => "unknown",
        }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Transfer {
    sender: Party,
    receiver: Party,
    timestamp: Option<String>,
    amount: Option<String>,
    currency: Option<String>,
    description: String,
    evidence: String,
    pub(super) page: u32,
    reference: Option<String>,
    confidence: Confidence,
    review_reason: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
enum Confidence {
    High,
    Medium,
    Low,
}

impl Confidence {
    fn as_str(&self) -> &'static str {
        match self {
            Self::High => "high",
            Self::Medium => "medium",
            Self::Low => "low",
        }
    }
}

fn cleaned(value: Option<&str>) -> std::result::Result<Option<&str>, String> {
    let value = value.map(str::trim).filter(|value| !value.is_empty());
    if value.is_some_and(|value| value.chars().count() > 512) {
        return Err("Party field exceeds 512 characters".into());
    }
    Ok(value)
}

fn party_node(party: &Party, sha256: &str) -> std::result::Result<Value, String> {
    static MASKED: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"(?i)[*•…x]|\b(last|ending)\b").expect("constant regex"));
    let name = cleaned(party.name.as_deref())?;
    let bank = cleaned(party.bank.as_deref())?;
    let account = cleaned(party.account.as_deref())?;
    let (id, basis) = match (name, bank, account) {
        (_, Some(bank), Some(account)) if !MASKED.is_match(account) => {
            (ascii_json(&json!([bank, account])), "bank_account")
        }
        (Some(name), _, _) => {
            let normalized: String = name.nfkc().case_fold().collect();
            let normalized = normalized.split_whitespace().collect::<Vec<_>>().join(" ");
            (format!("party:{}", hash(normalized.as_bytes())), "name")
        }
        (_, _, Some(account)) => {
            let value = format!(
                "[{}, {}, {}]",
                ascii_json(&json!(sha256)),
                ascii_json(&json!(bank)),
                ascii_json(&json!(account))
            );
            (
                format!("account:{}", hash(value.as_bytes())),
                "document_account",
            )
        }
        _ => return Err("Transfer has an unidentified sender or receiver".into()),
    };
    Ok(json!({
        "id": id, "label": name.or(account), "name": name, "bank": bank, "account": account,
        "kind": party.kind.as_str(), "identity_basis": basis,
        "resolution_status": if basis == "bank_account" { "exact_identifier" } else { "provisional" }
    }))
}

pub(super) struct ExtractionSource {
    pub(super) provider: Provider,
    pub(super) model: String,
    pub(super) resolved_model: String,
    pub(super) direct: bool,
}

pub(super) fn normalize_transfer(
    transfer: Transfer,
    sha256: &str,
    source: &ExtractionSource,
) -> std::result::Result<Value, String> {
    let normalize = || {
        let sender = party_node(&transfer.sender, sha256)?;
        let receiver = party_node(&transfer.receiver, sha256)?;
        if transfer.evidence.trim().is_empty() || transfer.evidence.chars().count() > 4000 {
            return Err("Transfer needs a short source evidence quote".into());
        }
        let amount = transfer
            .amount
            .as_deref()
            .map(decimal)
            .transpose()
            .map_err(|error| format!("Extracted amount {error}"))?;
        let raw_time = transfer
            .timestamp
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty());
        let timestamp = raw_time.map(normalize_time).transpose()?;
        let precision = raw_time.map(|value| {
            if value.len() == 10 {
                "date"
            } else {
                "datetime"
            }
        });
        let currency = cleaned(transfer.currency.as_deref())?;
        let mut reasons = Vec::new();
        let confidence = transfer.confidence.as_str();
        if confidence != "high" {
            reasons.push(format!("Extraction confidence is {confidence}"));
        }
        if let Some(reason) = transfer.review_reason.filter(|value| !value.is_empty()) {
            reasons.push(reason);
        }
        if sender["resolution_status"] == "provisional"
            || receiver["resolution_status"] == "provisional"
        {
            reasons.push("Party identity is provisional; name matches are candidate links, not verified identity".into());
        }
        if timestamp.is_none() || amount.is_none() || currency.is_none() {
            reasons.push("Date, amount, or currency is absent from the source".into());
        }
        Ok(json!({
            "record_type": "relation", "relation": "transfer", "sender_id": sender["id"], "receiver_id": receiver["id"],
            "sender": sender, "receiver": receiver, "timestamp": timestamp, "timestamp_precision": precision,
            "amount": amount, "currency": currency, "description": transfer.description, "reference": transfer.reference,
            "evidence": {"page": transfer.page, "quote": transfer.evidence},
            "extraction": {
                "provider": source.provider.name(), "model": source.model,
                "resolved_model": source.resolved_model, "mode": if source.direct { "direct" } else { "text" },
                "confidence": confidence, "verification": "unverified"
            },
            "review_required": !reasons.is_empty(), "review_reasons": reasons
        }))
    };
    normalize().map_err(|error: String| format!("PDF page {}: {error}", transfer.page))
}
