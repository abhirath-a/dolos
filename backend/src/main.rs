mod db;
mod documents;
mod graph;
mod ingest;
mod model;
mod storage;

use anyhow::{Context, Result};
use axum::{
    Json, Router,
    extract::{
        DefaultBodyLimit, FromRequest, FromRequestParts, Path, Query, Request, State,
        multipart::MultipartError,
    },
    http::{StatusCode, header, request::Parts},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use db::{Commands, GRAPH_ROWS, Queries, RowFilters, Store};
use graph::{analysis, entities, graph, str_field, timestamp};
use model::ModelRuntime;
use serde::{Deserialize, de::DeserializeOwned};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{HashMap, HashSet},
    env,
    path::PathBuf,
    sync::Arc,
    time::Duration,
};
use storage::Storage;
use subtle::ConstantTimeEq;
use tokio::sync::{OwnedSemaphorePermit, Semaphore};
use tower_http::services::{ServeDir, ServeFile};
use tracing::Instrument;

const MAX_HISTORY: i64 = 100_000;
const CASES: &str = include_str!("../../dolos/migrations/0002_famous_cases.json");
const DEMO: &str = include_str!("../../dolos/demo_assets/model-demo.json");
const DEMO_SOURCE: &str = "dolos/demo_assets/synthetic-transfers.csv";
const DEMO_MODELS: &str = "dolos/demo_assets/artifacts";
tokio::task_local! { static REQUEST_ID: String; }

#[derive(Clone)]
struct App {
    commands: Commands,
    queries: Queries,
    storage: Storage,
    http: reqwest::Client,
    key: Option<Arc<str>>,
    demo_only: bool,
    permits: Arc<Semaphore>,
    models: Option<Arc<ModelRuntime>>,
    demo_model: Option<Arc<ModelRuntime>>,
    demo_rows: Option<Arc<Vec<Value>>>,
    demo: Arc<Value>,
    dataset: Arc<Value>,
}

type Admission = Arc<OwnedSemaphorePermit>;

impl App {
    fn admit(&self) -> Result<Admission, ApiError> {
        self.permits
            .clone()
            .try_acquire_owned()
            .map(Arc::new)
            .map_err(|_| {
                ApiError::new(
                    StatusCode::SERVICE_UNAVAILABLE,
                    "Processing capacity is full. Retry the request shortly.",
                )
            })
    }
}

type Reply = Result<(StatusCode, Json<Value>), ApiError>;

#[derive(Debug)]
struct ApiError {
    status: StatusCode,
    detail: String,
}

impl ApiError {
    fn new(status: StatusCode, detail: impl Into<String>) -> Self {
        Self {
            status,
            detail: detail.into(),
        }
    }
    fn invalid(detail: impl Into<String>) -> Self {
        Self::new(StatusCode::UNPROCESSABLE_ENTITY, detail)
    }
    fn not_found(detail: impl Into<String>) -> Self {
        Self::new(StatusCode::NOT_FOUND, detail)
    }
    fn unavailable(error: anyhow::Error, detail: &str) -> Self {
        tracing::warn!(error_type = %error.root_cause(), "Service dependency unavailable");
        Self::new(StatusCode::SERVICE_UNAVAILABLE, detail)
    }
    fn multipart(error: MultipartError) -> Self {
        Self::new(error.status(), "Could not read the multipart upload.")
    }
}

impl<E: Into<anyhow::Error>> From<E> for ApiError {
    fn from(error: E) -> Self {
        let error = error.into();
        tracing::error!(error = %error, "API operation failed");
        Self::new(
            StatusCode::INTERNAL_SERVER_ERROR,
            "The API could not complete the request.",
        )
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let code = match self.status {
            StatusCode::UNAUTHORIZED => "unauthorized",
            StatusCode::NOT_FOUND => "not_found",
            StatusCode::CONFLICT => "conflict",
            StatusCode::GONE => "expired",
            StatusCode::PAYLOAD_TOO_LARGE => "payload_too_large",
            StatusCode::UNSUPPORTED_MEDIA_TYPE => "unsupported_media_type",
            StatusCode::UNPROCESSABLE_ENTITY => "invalid_request",
            StatusCode::SERVICE_UNAVAILABLE => "unavailable",
            StatusCode::INTERNAL_SERVER_ERROR => "internal_error",
            StatusCode::GATEWAY_TIMEOUT => "request_timeout",
            _ => "request_failed",
        };
        let request_id = REQUEST_ID
            .try_with(String::clone)
            .unwrap_or_else(|_| storage::id());
        let mut response = (
            self.status,
            Json(json!({"detail":self.detail,"code":code,"request_id":request_id})),
        )
            .into_response();
        if self.status == StatusCode::UNAUTHORIZED {
            response.headers_mut().insert(
                header::WWW_AUTHENTICATE,
                header::HeaderValue::from_static("Bearer"),
            );
        }
        response
    }
}

fn ok(data: Value) -> Reply {
    Ok((StatusCode::OK, Json(data)))
}

struct ValidJson<T>(T);

impl<S: Send + Sync, T: DeserializeOwned> FromRequest<S> for ValidJson<T> {
    type Rejection = ApiError;

    async fn from_request(request: Request, state: &S) -> Result<Self, ApiError> {
        Json::<T>::from_request(request, state)
            .await
            .map(|Json(value)| Self(value))
            .map_err(|error| {
                if error.status() == StatusCode::PAYLOAD_TOO_LARGE {
                    ApiError::new(
                        StatusCode::PAYLOAD_TOO_LARGE,
                        "Request body exceeds the size limit.",
                    )
                } else {
                    ApiError::invalid("Invalid JSON request.")
                }
            })
    }
}

struct ValidQuery<T>(T);

impl<S: Send + Sync, T: DeserializeOwned> FromRequestParts<S> for ValidQuery<T> {
    type Rejection = ApiError;

    async fn from_request_parts(parts: &mut Parts, state: &S) -> Result<Self, ApiError> {
        Query::<T>::from_request_parts(parts, state)
            .await
            .map(|Query(value)| Self(value))
            .map_err(|_| ApiError::invalid("Invalid query parameters."))
    }
}

async fn authorize(State(app): State<App>, request: Request, next: Next) -> Response {
    let id = storage::id();
    let span = tracing::info_span!("api", request_id = %id, method = %request.method());
    REQUEST_ID
        .scope(id, authorize_request(app, request, next))
        .instrument(span)
        .await
}

async fn authorize_request(app: App, request: Request, next: Next) -> Response {
    if let Some(key) = &app.key {
        let supplied = request
            .headers()
            .get(header::AUTHORIZATION)
            .and_then(|value| value.to_str().ok())
            .and_then(|value| value.split_once(' '))
            .filter(|(scheme, _)| scheme.eq_ignore_ascii_case("bearer"))
            .map(|(_, token)| token);
        if !supplied.is_some_and(|value| bool::from(value.as_bytes().ct_eq(key.as_bytes()))) {
            return ApiError::new(StatusCode::UNAUTHORIZED, "Invalid bearer token").into_response();
        }
    }
    match tokio::time::timeout(Duration::from_secs(110), next.run(request)).await {
        Ok(response) => response,
        Err(_) => ApiError::new(
            StatusCode::GATEWAY_TIMEOUT,
            "The request time limit expired. Retry with a smaller batch.",
        )
        .into_response(),
    }
}

fn routes(app: &App) -> Router {
    let api = Router::new()
        .route("/health", get(health))
        .route("/demo/model/status", get(demo_status))
        .route("/demo/model/analyze", post(demo_analyze))
        .route("/demo/cases", get(demo_cases))
        .route("/demo/cases/{case_id}", get(demo_case));
    let api = if app.demo_only {
        api
    } else {
        api.route("/statement-schema", get(schema))
            .route(
                "/batches",
                post(documents::upload).layer(DefaultBodyLimit::max(
                    storage::BATCH_LIMIT as usize + 1024 * 1024,
                )),
            )
            .route("/uploads/presign", post(documents::presign))
            .route("/batches/{batch_id}/complete", post(documents::complete))
            .route("/batches/{batch_id}", get(documents::get_batch))
            .route("/documents/{document_id}/retry", post(documents::retry))
            .route("/documents/{document_id}/errors", get(row_errors))
            .route("/transactions", get(transactions))
            .route("/relations", get(relations))
            .route("/graph", get(get_graph))
            .route("/scores", get(scores))
            .route("/analyze", post(analyze))
    };
    let api = api
        .route_layer(middleware::from_fn_with_state(app.clone(), authorize))
        .with_state(app.clone());
    let dir =
        PathBuf::from(env::var("DOLOS_FRONTEND_DIR").unwrap_or_else(|_| "frontend/dist".into()));
    let index = dir.join("index.html");
    let router = Router::new()
        .nest(
            "/api",
            api.clone()
                .fallback(|| async { ApiError::not_found("API route not found") }),
        )
        .merge(api);
    if index.is_file() {
        router.fallback_service(ServeDir::new(dir).not_found_service(ServeFile::new(index)))
    } else {
        router.route(
            "/",
            get(|| async {
                ApiError::new(
                    StatusCode::SERVICE_UNAVAILABLE,
                    "Build the frontend first: cd frontend && pnpm build",
                )
            }),
        )
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "dolos_api=info".into()),
        )
        .init();
    let demo: Value = serde_json::from_str(DEMO)?;
    let demo_source =
        PathBuf::from(env::var("DOLOS_DEMO_SOURCE_CSV").unwrap_or_else(|_| DEMO_SOURCE.into()));
    let demo_dir =
        PathBuf::from(env::var("DOLOS_DEMO_MODEL_DIR").unwrap_or_else(|_| DEMO_MODELS.into()));
    let demo_manifest = std::fs::read(demo_dir.join("manifest.json"))
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok());
    let demo_verified = demo_manifest.is_some_and(|m| m.get("run_id") == demo.get("run_id"))
        && std::fs::read(&demo_source)
            .ok()
            .is_some_and(|b| format!("{:x}", Sha256::digest(b)) == demo["source_sha256"]);
    let demo_model = demo_verified.then(|| try_load(&demo_dir)).flatten();
    let models = env::var_os("DOLOS_MODEL_DIR")
        .filter(|dir| !dir.is_empty())
        .and_then(|dir| {
            let dir = PathBuf::from(dir);
            if let Some(runtime) = &demo_model
                && dir.canonicalize().ok() == demo_dir.canonicalize().ok()
            {
                return Some(runtime.clone());
            }
            try_load(&dir)
        });
    let http = reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(10))
        .build()?;
    let demo_rows = if demo_model.is_some() {
        match ingest::parse_document_with_guard(&demo_source, ".csv", &http, None).await {
            Ok(rows) => Some(Arc::new(
                rows.into_iter()
                    .filter_map(|row| {
                        let mut data = row.row.ok()?.into_payload();
                        data["id"] = format!("simulation:{}", row.index).into();
                        data["document_id"] = "synthetic-transfers.csv".into();
                        data["source_row"] = row.index.into();
                        Some(data)
                    })
                    .collect(),
            )),
            Err(error) => {
                tracing::warn!(%error, "Demo statement unavailable");
                None
            }
        }
    } else {
        None
    };
    let (commands, queries) = Store::open().await?.split();
    let app = App {
        commands,
        queries,
        demo_only: env::var("DOLOS_DEMO_ONLY").is_ok_and(|value| value == "1"),
        storage: Storage::open().await?,
        http,
        key: env::var("DOLOS_API_KEY")
            .ok()
            .filter(|key| !key.is_empty())
            .map(Arc::from),
        permits: Arc::new(Semaphore::new(
            env::var("DOLOS_PROCESSING_LIMIT")
                .ok()
                .and_then(|value| value.parse::<usize>().ok())
                .filter(|&v| (1..=32).contains(&v))
                .unwrap_or(4),
        )),
        models,
        demo: Arc::new(demo),
        demo_model,
        demo_rows,
        dataset: Arc::new(serde_json::from_str(CASES)?),
    };
    let listener = tokio::net::TcpListener::bind(
        env::var("DOLOS_BIND").unwrap_or_else(|_| "127.0.0.1:8000".into()),
    )
    .await?;
    tracing::info!(address = %listener.local_addr()?, "Dolos API listening");
    axum::serve(listener, routes(&app))
        .with_graceful_shutdown(shutdown())
        .await?;
    Ok(())
}

fn try_load(dir: &std::path::Path) -> Option<Arc<ModelRuntime>> {
    match ModelRuntime::load(dir) {
        Ok(runtime) => Some(Arc::new(runtime)),
        Err(error) => {
            tracing::warn!(%error, "Model bundle unavailable");
            None
        }
    }
}

async fn shutdown() {
    let ctrl_c = tokio::signal::ctrl_c();
    #[cfg(unix)]
    {
        if let Ok(mut terminate) =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
        {
            tokio::select! { _ = ctrl_c => {}, _ = terminate.recv() => {} }
            return;
        }
    }
    let _ = ctrl_c.await;
}

async fn health(State(app): State<App>) -> Reply {
    app.queries.ready().await?;
    let enabled = |name| {
        app.models
            .as_ref()
            .is_some_and(|model| model.has_model(name))
    };
    let available: Vec<_> = ["hybrid"]
        .into_iter()
        .filter(|&name| enabled(name))
        .collect();
    let components: Vec<_> = ["graphsage", "graph_xgboost"]
        .into_iter()
        .filter(|&name| enabled(name))
        .collect();
    ok(
        json!({"status":"ok","pending_documents":0,"model_configured":!available.is_empty(),
        "available_models":available,"component_artifacts":components,"uploads_enabled":!app.demo_only}),
    )
}

async fn schema() -> Reply {
    ok(
        json!({"required_columns":ingest::FIELDS,"optional_columns":["reference"],
        "timestamp_policy":"ISO 8601 with offset; naive timestamps assumed UTC. IBM YYYY/MM/DD HH:MM accepted.",
        "amount_policy":"Nonnegative decimal strings; sender pays amount_paid, receiver gets amount_received.",
        "identity_policy":"Exact bank/account strings; no fuzzy matching or counterparty guessing.",
        "pdf_support":"Flexible PDF-to-relation extraction through the API. Names alone can form graph nodes; missing fields remain null."}),
    )
}

#[derive(Default, Deserialize)]
struct Page {
    limit: Option<i64>,
    offset: Option<i64>,
    account: Option<String>,
    before: Option<String>,
    batch_id: Option<String>,
    after: Option<String>,
}

fn page(query: &Page, default: i64, max: i64) -> Result<(i64, i64), ApiError> {
    let limit = query.limit.unwrap_or(default);
    let offset = query.offset.unwrap_or_default();
    if !(1..=max).contains(&limit) || offset < 0 || offset > i64::MAX - max {
        return Err(ApiError::invalid("Invalid limit or offset."));
    }
    Ok((limit, offset))
}

async fn row_errors(
    State(app): State<App>,
    Path(id): Path<String>,
    ValidQuery(query): ValidQuery<Page>,
) -> Reply {
    let (limit, offset) = page(&query, 100, 1000)?;
    let rows = app
        .queries
        .row_errors(&id, limit, offset)
        .await?
        .ok_or_else(|| ApiError::not_found("Document not found"))?;
    ok(json!({"errors":rows}))
}

async fn get_rows(app: &App, query: Page, source: &'static str) -> Reply {
    let is_graph = source == GRAPH_ROWS;
    let (limit, offset) = page(&query, if is_graph { 1000 } else { 100 }, 10_000)?;
    let parse = |value: Option<&str>, name| {
        value
            .filter(|s| !s.is_empty())
            .map(|value| {
                timestamp(value).ok_or_else(|| {
                    ApiError::invalid(format!("{name} must be an ISO 8601 timestamp"))
                })
            })
            .transpose()
    };
    let before = parse(query.before.as_deref(), "before")?;
    let after = if is_graph {
        parse(query.after.as_deref(), "after")?
    } else {
        None
    };
    if let (Some(after), Some(before)) = (&after, &before)
        && after >= before
    {
        return Err(ApiError::invalid("after must precede before"));
    }
    let mut rows = app
        .queries
        .rows(
            source,
            RowFilters {
                limit: limit + 1,
                offset,
                account: query.account.as_deref(),
                before: before.as_deref(),
                after: after.as_deref(),
                batch: query.batch_id.as_deref(),
                ..Default::default()
            },
        )
        .await?;
    let has_more = rows.len() > limit as usize;
    rows.truncate(limit as usize);
    let mut value = if is_graph {
        let mut data = graph(&rows);
        data["scope"] =
            "Summaries cover only the returned page; after is inclusive and before is exclusive."
                .into();
        data
    } else {
        json!({(source):rows})
    };
    value["has_more"] = has_more.into();
    value["next_offset"] = json!(has_more.then_some(offset + limit));
    ok(value)
}

async fn transactions(State(app): State<App>, ValidQuery(query): ValidQuery<Page>) -> Reply {
    get_rows(&app, query, "transactions").await
}
async fn relations(State(app): State<App>, ValidQuery(query): ValidQuery<Page>) -> Reply {
    get_rows(&app, query, "relations").await
}
async fn get_graph(State(app): State<App>, ValidQuery(query): ValidQuery<Page>) -> Reply {
    get_rows(&app, query, GRAPH_ROWS).await
}

fn cases(app: &App) -> impl Iterator<Item = &Value> {
    app.dataset["cases"].as_array().into_iter().flatten()
}

async fn demo_cases(State(app): State<App>) -> Reply {
    let cases = cases(&app)
        .cloned()
        .map(|mut case| {
            if let Some(obj) = case.as_object_mut() {
                obj.remove("nodes");
                obj.remove("edges");
            }
            case
        })
        .collect::<Vec<_>>();
    ok(
        json!({"dataset_id":app.dataset["dataset_id"],"researched_on":app.dataset["researched_on"],
        "notes":app.dataset["notes"],"sources":app.dataset["sources"],"cases":cases}),
    )
}

async fn demo_case(State(app): State<App>, Path(id): Path<String>) -> Reply {
    let mut case = cases(&app)
        .find(|c| c["id"] == id)
        .cloned()
        .ok_or_else(|| ApiError::not_found("Demo case not found"))?;
    let sources = app.dataset["sources"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|source| {
            case["source_ids"]
                .as_array()
                .is_some_and(|ids| ids.contains(&source["id"]))
        })
        .cloned()
        .collect::<Vec<_>>();
    case["sources"] = sources.into();
    case["dataset_notes"] = app.dataset["notes"].clone();
    ok(case)
}

fn historical_rows(app: &App, id: &str) -> Result<Vec<Value>, ApiError> {
    let case = cases(app)
        .find(|case| case["id"] == id)
        .ok_or_else(|| ApiError::not_found("Case not found"))?;
    let sources: HashMap<_, _> = app.dataset["sources"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|s| (str_field(s, "id"), s))
        .collect();
    let nodes: HashMap<_, _> = case["nodes"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|node| {
            let mut payload = node.clone();
            payload["name"] = node["label"].clone();
            payload["bank"] = Value::Null;
            payload["account"] = Value::Null;
            payload["identity_basis"] = "curated_case_entity".into();
            payload["resolution_status"] = "source_described".into();
            payload["case_id"] = id.into();
            (str_field(node, "id"), payload)
        })
        .collect();
    Ok(case["edges"].as_array().into_iter().flatten().enumerate().map(|(i,edge)| {
        let sender = str_field(edge,"source");
        let receiver = str_field(edge,"target");
        let citations: Vec<_> = edge["citations"].as_array().into_iter().flatten().cloned().map(|mut citation| {
            if let Some(source) = sources.get(str_field(&citation,"source_id")) {
                citation["url"] = source["url"].clone();
                citation["published"] = source["published"].clone();
            }
            citation
        }).collect();
        json!({"id":edge["id"],"document_id":null,"source_row":i+1,"record_type":"relation",
            "relation":edge["relation"],"case_id":id,"case_title":case["title"],"dataset_id":app.dataset["dataset_id"],
            "sender_id":sender,"receiver_id":receiver,"sender":nodes.get(sender),"receiver":nodes.get(receiver),
            "timestamp":edge["event_date"].as_str().and_then(timestamp),
            "timestamp_precision":edge["event_date"].as_str().map(|_| "date"),
            "date_qualifier":edge["date_qualifier"],"event_period":case["period"],"amount":edge["amount"],
            "currency":edge["currency"],"amount_scope":edge["amount_scope"],"amount_qualifier":edge["amount_qualifier"],
            "description":edge["description"],"reference":edge["id"],"evidence_status":edge["evidence_status"],
            "citations":citations,"evidence":{"page":null,"quote":null,"kind":"source_paraphrase","citations":citations},
            "extraction":{"provider":"curated_public_sources","model":null,"mode":"historical_demo","verification":"source_linked_not_independently_adjudicated"},
            "review_required":true,"review_reasons":[case["caveat"]]})
    }).collect())
}

fn default_model() -> String {
    "hybrid".into()
}

#[derive(Deserialize)]
struct ModelQuery {
    #[serde(default = "default_model")]
    model: String,
    example: Option<String>,
    limit: Option<i64>,
}

const MAIN_MODELS: &[&str] = &[
    "hybrid",
    "graph_xgboost",
    "transaction_xgboost",
    "graphsage",
];
const DEMO_MODEL_NAMES: &[&str] = &["hybrid", "graph_xgboost", "graphsage"];
const SCORED_MODELS: &[&str] = &[
    "hybrid",
    "graph_xgboost",
    "transaction_xgboost",
    "graphsage",
    "graphsage_no_neighbors",
];

fn validate_model(model: &str, choices: &[&str]) -> Result<(), ApiError> {
    if !choices.contains(&model) {
        return Err(ApiError::invalid("Invalid model name."));
    }
    Ok(())
}

fn unavailable_model() -> ApiError {
    ApiError::new(
        StatusCode::SERVICE_UNAVAILABLE,
        "The selected ONNX model artifacts are unavailable.",
    )
}

async fn demo_status(State(app): State<App>) -> Reply {
    let ready = app.demo_rows.is_some()
        && app
            .demo_model
            .as_ref()
            .is_some_and(|model| model.has_model("hybrid"));
    ok(json!({"live_available":ready,"recorded_available":true,
        "reason":(!ready).then_some("Matching live ONNX demo artifacts are unavailable."),
        "data_type":"synthetic_teaching_data"}))
}

async fn demo_analyze(State(app): State<App>, ValidQuery(query): ValidQuery<ModelQuery>) -> Reply {
    validate_model(&query.model, DEMO_MODEL_NAMES)?;
    let permit = app.admit()?;
    let targets: HashSet<String> =
        if let Some(example) = query.example.filter(|value| !value.is_empty()) {
            if example.chars().count() > 80 {
                return Err(ApiError::invalid("Example ID is too long."));
            }
            let entry = app.demo["examples"]
                .as_array()
                .into_iter()
                .flatten()
                .find(|entry| entry["id"] == example)
                .ok_or_else(|| ApiError::not_found("Unknown model example."))?;
            entry["focus_edge_ids"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect()
        } else {
            HashSet::from([str_field(&app.demo, "focus_edge_id").to_owned()])
        };
    let runtime = app
        .demo_model
        .clone()
        .filter(|m| m.has_model(&query.model))
        .ok_or_else(unavailable_model)?;
    let rows = app.demo_rows.clone().ok_or_else(unavailable_model)?;
    let limit = targets.len();
    let name = query.model.clone();
    let mut result = infer(
        runtime,
        rows,
        query.model,
        limit,
        Some(targets),
        true,
        permit,
    )
    .await?;
    result["status"] = "completed".into();
    result["name"] = name.into();
    result["threshold"] = 0.into();
    ok(result)
}

async fn history(app: &App) -> Result<Vec<Value>, ApiError> {
    let rows = app
        .queries
        .rows(
            "transactions",
            RowFilters {
                limit: MAX_HISTORY + 1,
                ..Default::default()
            },
        )
        .await?;
    if rows.len() > MAX_HISTORY as usize {
        return Err(ApiError::new(
            StatusCode::PAYLOAD_TOO_LARGE,
            "Model history exceeds 100,000 rows; offline scoring required.",
        ));
    }
    Ok(rows)
}

async fn infer(
    runtime: Arc<ModelRuntime>,
    rows: Arc<Vec<Value>>,
    name: String,
    limit: usize,
    targets: Option<HashSet<String>>,
    explain: bool,
    permit: Admission,
) -> Result<Value, ApiError> {
    let result = tokio::task::spawn_blocking(move || {
        let _permit = permit;
        runtime.score(&rows, &name, limit, targets.as_ref(), explain)
    })
    .await?;
    result.map_err(|err| {
        ApiError::unavailable(
            err,
            "The selected ONNX model could not score these records.",
        )
    })
}

async fn scores(State(app): State<App>, ValidQuery(query): ValidQuery<ModelQuery>) -> Reply {
    validate_model(&query.model, SCORED_MODELS)?;
    let limit = query.limit.unwrap_or(100);
    if !(1..=1000).contains(&limit) {
        return Err(ApiError::invalid(
            "Scoring limit must be between 1 and 1000.",
        ));
    }
    let permit = app.admit()?;
    let runtime = app
        .models
        .clone()
        .filter(|m| m.has_model(&query.model))
        .ok_or_else(unavailable_model)?;
    let rows = history(&app).await?;
    ok(infer(
        runtime,
        Arc::new(rows),
        query.model,
        limit as usize,
        None,
        false,
        permit,
    )
    .await?)
}

#[derive(Deserialize)]
struct AnalysisRequest {
    #[serde(default)]
    batch_ids: Vec<String>,
    case_id: Option<String>,
    #[serde(default)]
    edge_ids: Vec<String>,
    #[serde(default = "default_model")]
    model: String,
}

async fn analyze(State(app): State<App>, ValidJson(request): ValidJson<AnalysisRequest>) -> Reply {
    validate_model(&request.model, MAIN_MODELS)?;
    if request.batch_ids.len() > 1000 || request.edge_ids.len() > 2000 {
        return Err(ApiError::invalid(
            "Too many selected batches or relationships.",
        ));
    }
    let permit = app.admit()?;
    let case = request.case_id.as_deref().filter(|id| !id.is_empty());
    let historical = case.is_some();
    let mut rows = if let Some(id) = case {
        if !request.batch_ids.is_empty() {
            return Err(ApiError::invalid(
                "Select either a historical case or uploaded batches.",
            ));
        }
        historical_rows(&app, id)?
    } else {
        if request.batch_ids.is_empty() {
            return Err(ApiError::invalid("Select at least one uploaded batch."));
        }
        if !app.queries.batches_exist(&request.batch_ids).await? {
            return Err(ApiError::not_found("Batch not found"));
        }
        let selected = app
            .queries
            .rows(
                GRAPH_ROWS,
                RowFilters {
                    limit: 2001,
                    batches: &request.batch_ids,
                    edge_ids: &request.edge_ids,
                    ..Default::default()
                },
            )
            .await?;
        if selected.len() > 2000 {
            return Err(ApiError::new(
                StatusCode::PAYLOAD_TOO_LARGE,
                "Analysis supports 2,000 visible relationships at once. Send edge_ids to analyze a graph slice.",
            ));
        }
        selected
    };
    rows.sort_by(|a, b| {
        (str_field(a, "timestamp"), str_field(a, "id"))
            .cmp(&(str_field(b, "timestamp"), str_field(b, "id")))
    });
    let graph = graph(&rows);
    let mut response = analysis(&graph, historical);
    let candidates: HashSet<_> = rows
        .iter()
        .filter(|row| row["record_type"] != "relation")
        .map(|row| str_field(row, "id").to_owned())
        .collect();
    let eligible = candidates.len();
    let name = request.model;
    response["model"] = json!({"status":"not_applicable","name":name,"reason":if historical {
        "Historical relationships are source-linked case evidence, not model input."
    } else { "Extracted relationships lack complete canonical model fields. Pattern checks ran instead." }});
    if eligible > 0 {
        let model = app.models.clone().filter(|m| m.has_model(&name));
        if let Some(runtime) = model {
            match infer(
                runtime,
                Arc::new(history(&app).await?),
                name.clone(),
                2000,
                Some(candidates),
                true,
                permit.clone(),
            )
            .await
            {
                Ok(mut result) => {
                    result["status"] = "completed".into();
                    result["name"] = name.clone().into();
                    result["threshold"] = 0.into();
                    result["decision_policy"] = "Raw margin above zero is the classifier positive decision; not a calibrated probability.".into();
                    merge_findings(&mut response, &graph, &result, &name)?;
                    response["model"] = result;
                }
                Err(error) => {
                    response["model"] =
                        json!({"status":"unavailable","name":name,"reason":error.detail});
                }
            }
        } else {
            response["model"] =
                json!({"status":"unavailable","name":name,"reason":unavailable_model().detail});
        }
    }
    let count = response["findings"].as_array().map(Vec::len).unwrap_or(0);
    response["summary"] = if count == 0 {
        "No configured rules or available predictions flagged these relationships.".into()
    } else {
        format!("{count} relationships highlighted for review.").into()
    };
    response["scope"] =
        json!({"relationships":rows.len(),"historical":historical,"model_eligible":eligible});
    ok(response)
}

fn merge_findings(response: &mut Value, graph: &Value, result: &Value, name: &str) -> Result<()> {
    let scores = result["scores"]
        .as_array()
        .context("Model scores unavailable")?;
    if name == "hybrid" && !scores.is_empty() {
        response["pattern_observations"] = std::mem::replace(&mut response["findings"], json!([]));
        response["method"] = "hybrid_model".into();
    }
    let graph: HashMap<_, _> = graph["edges"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|edge| (str_field(edge, "id"), edge))
        .collect();
    let findings = response["findings"]
        .as_array_mut()
        .context("Analysis findings unavailable")?;
    let mut indices: HashMap<String, usize> = findings
        .iter()
        .enumerate()
        .map(|(i, f)| (str_field(f, "edge_id").to_owned(), i))
        .collect();
    for score in scores {
        if let Some(margin) = score["score"].as_f64().filter(|margin| *margin > 0.0) {
            let id = str_field(score, "transaction_id");
            let Some(edge) = graph.get(id) else {
                continue;
            };
            let i = *indices.entry(id.to_owned()).or_insert_with(|| {
                let i = findings.len();
                findings.push(json!({"edge_id":id,"source":edge["source"],"target":edge["target"],"reasons":[]}));
                i
            });
            if let Some(reasons) = findings[i]["reasons"].as_array_mut() {
                reasons.push(json!({"rule":"Model positive decision","detail":format!("Raw margin {margin:.4} exceeds the classifier boundary of zero.")}));
            }
        }
    }
    response["entities"] = entities(findings).into();
    Ok(())
}
