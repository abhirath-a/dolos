//! Deterministic canonical statement interpretation shared by the format adapters.
use std::borrow::Cow;

use chrono::{DateTime, NaiveDate, NaiveDateTime, SecondsFormat, Utc};
use serde::Serialize;
use serde_json::{Map, Value};

pub const FIELDS: [&str; 10] = [
    "timestamp",
    "from_bank",
    "from_account",
    "to_bank",
    "to_account",
    "amount_received",
    "receiving_currency",
    "amount_paid",
    "payment_currency",
    "payment_format",
];

#[derive(Clone, Copy)]
enum Field<'a> {
    Missing,
    Text(&'a str),
    Number(&'a serde_json::Number),
}

/// The canonical observation IR keeps source types and values until validation.
pub(super) struct CanonicalInput<'a> {
    fields: [Field<'a>; FIELDS.len()],
    reference: Option<Cow<'a, str>>,
}

#[derive(Serialize)]
struct CanonicalTransfer<'a> {
    timestamp: String,
    from_bank: Cow<'a, str>,
    from_account: Cow<'a, str>,
    to_bank: Cow<'a, str>,
    to_account: Cow<'a, str>,
    amount_received: String,
    receiving_currency: Cow<'a, str>,
    amount_paid: String,
    payment_currency: Cow<'a, str>,
    payment_format: Cow<'a, str>,
    sender_id: String,
    receiver_id: String,
    reference: Option<Cow<'a, str>>,
}

impl<'a> CanonicalInput<'a> {
    pub(super) fn csv(fields: [&'a str; FIELDS.len()], reference: Option<&'a str>) -> Self {
        Self {
            fields: fields.map(Field::Text),
            reference: reference.map(Cow::Borrowed),
        }
    }

    pub(super) fn json(row: &'a Map<String, Value>) -> Result<Self, String> {
        if let Some(error) = row.get("_error") {
            return Err(error
                .as_str()
                .map_or_else(|| error.to_string(), str::to_owned));
        }
        Ok(Self {
            fields: FIELDS.map(|name| match row.get(name) {
                Some(Value::String(value)) => Field::Text(value),
                Some(Value::Number(value)) => Field::Number(value),
                _ => Field::Missing,
            }),
            reference: row
                .get("reference")
                .filter(|value| !value.is_null())
                .map(|value| {
                    value
                        .as_str()
                        .map_or_else(|| Cow::Owned(value.to_string()), Cow::Borrowed)
                }),
        })
    }

    pub(super) fn interpret(self) -> Result<Value, String> {
        let mut fields = self.fields.into_iter().zip(FIELDS);
        let mut field = || {
            let (value, name) = fields.next().expect("canonical field count");
            let identity = matches!(
                name,
                "from_bank" | "from_account" | "to_bank" | "to_account"
            );
            let value = match value {
                Field::Text(value) => Cow::Borrowed(value.trim()),
                Field::Number(value) if !identity => Cow::Owned(value.to_string()),
                _ if identity => {
                    return Err(format!(
                        "{name} must be a string to preserve account identity"
                    ));
                }
                _ => return Err(format!("{name} is required")),
            };
            if value.is_empty() || (value.len() > 256 && value.chars().count() > 256) {
                return Err(format!("{name} must contain 1–256 characters"));
            }
            Ok(value)
        };
        let timestamp = field()?;
        let from_bank = field()?;
        let from_account = field()?;
        let to_bank = field()?;
        let to_account = field()?;
        let amount_received = field()?;
        let receiving_currency = field()?;
        let amount_paid = field()?;
        let payment_currency = field()?;
        let payment_format = field()?;

        let timestamp = normalize_time(&timestamp)?;
        // Match the reference implementation's validation order.
        let amount_paid = decimal(&amount_paid).map_err(|error| format!("amount_paid {error}"))?;
        let amount_received =
            decimal(&amount_received).map_err(|error| format!("amount_received {error}"))?;
        let sender_id = identity(&from_bank, &from_account);
        let receiver_id = identity(&to_bank, &to_account);
        let reference = self.reference.map(|value| match value {
            Cow::Borrowed(text) => Cow::Borrowed(text.trim()),
            value => value,
        });
        serde_json::to_value(CanonicalTransfer {
            timestamp,
            from_bank,
            from_account,
            to_bank,
            to_account,
            amount_received,
            receiving_currency,
            amount_paid,
            payment_currency,
            payment_format,
            sender_id,
            receiver_id,
            reference,
        })
        .map_err(|error| error.to_string())
    }
}

fn identity(bank: &str, account: &str) -> String {
    ascii_encoding(serde_json::to_string(&[bank, account]).expect("strings serialize"))
}

pub(super) fn ascii_json(value: &Value) -> String {
    ascii_encoding(value.to_string())
}

fn ascii_encoding(text: String) -> String {
    if text.is_ascii() && !text.as_bytes().contains(&127) {
        return text;
    }
    use std::fmt::Write;
    let mut result = String::new();
    for value in text.chars() {
        if value.is_ascii() && value != '\u{7f}' {
            result.push(value);
        } else {
            for unit in value.encode_utf16(&mut [0; 2]) {
                let _ = write!(result, "\\u{unit:04x}");
            }
        }
    }
    result
}

pub(crate) fn normalize_time(value: &str) -> std::result::Result<String, String> {
    let value = value.trim();
    let split = value.get(10..11);
    let iso = if split == Some(" ") {
        value.replacen(' ', "T", 1)
    } else {
        value.to_owned()
    };
    if let Ok(time) = DateTime::parse_from_rfc3339(&iso) {
        return Ok(time
            .with_timezone(&Utc)
            .to_rfc3339_opts(SecondsFormat::Micros, false));
    }
    for format in ["%Y-%m-%dT%H:%M:%S%.f%:z", "%Y-%m-%dT%H:%M%:z"] {
        if let Ok(time) = DateTime::parse_from_str(&iso, format) {
            return Ok(time
                .with_timezone(&Utc)
                .to_rfc3339_opts(SecondsFormat::Micros, false));
        }
    }
    let naive = ["%Y-%m-%dT%H:%M:%S%.f", "%Y-%m-%dT%H:%M", "%Y/%m/%dT%H:%M"]
        .iter()
        .find_map(|format| NaiveDateTime::parse_from_str(&iso, format).ok())
        .or_else(|| {
            NaiveDate::parse_from_str(value, "%Y-%m-%d")
                .ok()?
                .and_hms_opt(0, 0, 0)
        })
        .ok_or_else(|| "timestamp must be a valid ISO 8601 or IBM timestamp".to_owned())?;
    Ok(naive
        .and_utc()
        .to_rfc3339_opts(SecondsFormat::Micros, false))
}

pub(super) fn decimal(value: &str) -> std::result::Result<String, &'static str> {
    const FORMAT: &str = "must be a decimal";
    const RANGE: &str = "must be finite and between 0 and 1e18";
    const PRECISION: &str = "supports at most 18 fractional digits and 38 significant digits";

    let value = value.trim();
    let negative = value.starts_with('-');
    let unsigned = value.strip_prefix(['+', '-']).unwrap_or(value);
    let (mantissa, exponent) = match unsigned.split_once(['e', 'E']) {
        Some((mantissa, exponent)) => (mantissa, exponent.parse::<i32>().map_err(|_| FORMAT)?),
        None => (unsigned, 0),
    };
    let (whole, fraction) = mantissa.split_once('.').unwrap_or((mantissa, ""));
    if mantissa.is_empty()
        || (whole.is_empty() && fraction.is_empty())
        || !whole
            .bytes()
            .chain(fraction.bytes())
            .all(|byte| byte.is_ascii_digit())
    {
        return Err(FORMAT);
    }
    let digits = format!("{whole}{fraction}");
    let digits = digits.trim_start_matches('0');
    let digits = if digits.is_empty() { "0" } else { digits };
    let scale = i64::from(exponent) - fraction.len() as i64;
    let point = digits.len() as i64 + scale;
    if digits != "0"
        && (negative
            || point > 19
            || (point == 19
                && (digits.as_bytes()[0] != b'1'
                    || digits.as_bytes()[1..].iter().any(|byte| *byte != b'0'))))
    {
        return Err(RANGE);
    }
    if !(-18..=18).contains(&scale) || digits.len() > 38 {
        return Err(PRECISION);
    }
    let mut result = if negative {
        "-".to_owned()
    } else {
        String::new()
    };
    if point <= 0 {
        result.push_str("0.");
        result.push_str(&"0".repeat((-point) as usize));
        result.push_str(digits);
    } else if point < digits.len() as i64 {
        let point = point as usize;
        result.push_str(&digits[..point]);
        result.push('.');
        result.push_str(&digits[point..]);
    } else {
        result.push_str(digits);
        if digits != "0" {
            result.push_str(&"0".repeat(scale as usize));
        }
    }
    Ok(result)
}
