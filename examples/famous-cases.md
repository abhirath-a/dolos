# Famous-case graph data

Six public-source case graphs, researched 2026-11-15: **33 nodes, 29 edges, seven primary sources**. The machine-readable dataset is [`dolos/migrations/0002_famous_cases.json`](../dolos/migrations/0002_famous_cases.json).

| Case id | Demo angle | Source |
| --- | --- | --- |
| `al-capone` | Criminal businesses and the tax case; historical context rather than a proven laundering trail. | [FBI history](https://www.fbi.gov/history/cases-and-criminals/al-capone), [National Archives verdict](https://www.archives.gov/chicago/highlights/capone) |
| `1mdb` | Good Star, Jho Low, Red Granite, and Hollywood financing. | [DOJ civil complaint](https://www.justice.gov/opa/file/879426/dl?inline=), pp. 15, 27, 107, paragraphs 43–46, 79, 385–388 |
| `hsbc` | Cartel proceeds and correspondent banking failures. | [DOJ 2012 resolution](https://www.justice.gov/archives/opa/pr/hsbc-holdings-plc-and-hsbc-bank-usa-na-admit-anti-money-laundering-and-sanctions-violations) |
| `liberty-reserve` | Criminal clientele using a digital payment hub. | [DOJ 2016 sentencing account](https://www.justice.gov/usao-sdny/pr/liberty-reserve-founder-arthur-budovsky-sentenced-manhattan-federal-court-20-years) |
| `onecoin` | Investor receipts, commissions, luxury goods, and a yacht payment. | [DOJ 2023 sentencing account](https://www.justice.gov/usao-sdny/pr/co-founder-multibillion-dollar-cryptocurrency-scheme-onecoin-sentenced-20-years-prison) |
| `bitfinex` | Stolen bitcoin, mixing services, and gold. | [DOJ 2024 sentencing account](https://www.justice.gov/archives/opa/pr/bitfinex-hacker-sentenced-money-laundering-conspiracy-involving-billions-stolen) |

## Explore through the API

```sh
# Apply schema and historical-case migrations to the configured database.
.venv/bin/python -m dolos.migrate

# Start the API against the same configured database.
.venv/bin/uvicorn dolos.api:app --host 127.0.0.1 --port 8000

curl http://127.0.0.1:8000/demo/cases
curl http://127.0.0.1:8000/demo/cases/1mdb
curl http://127.0.0.1:8000/graph
# Use a batch_id printed by the loader to isolate one case:
curl 'http://127.0.0.1:8000/graph?batch_id=BATCH_ID'
```

All six cases are installed together by migration 0002. Use batch ids from `/graph` or `/relations` to isolate a case in graph queries. `python -m dolos.demo --list` lists cases without writing; the old `dolos-demo` command now delegates to migrations. No API keys, LLM calls, or worker are needed. `/demo/cases` describes the bundled dataset even before migration; `/graph` shows records installed in the configured database. `DOLOS_DATABASE_URL` / `TURSO_DATABASE_URL` selects the database, independently from the upload directory. Migration checksums make the JSON an immutable snapshot; add future cases in a new migration.

## Evidence rules

These are curated case graphs, **not bank statements, a complete transaction ledger, or labeled AML training data**. No invented historical transfers or account numbers are included. Named account nodes are source labels, not recovered account identifiers. Cohort/category nodes group source-described activity and do not identify every member.

Every edge includes its source and locator, `evidence_status`, `amount_scope`, `amount_qualifier`, and any date qualifier. A 2016 complaint allegation stays an allegation. A sentencing account describes that publication's historical record, not a claim about someone's custody or case status today. Capone's verdict is tax evasion; the dataset does not assert a laundering conviction or validate the laundromat-origin story.

An exact published amount can still be an aggregate. Lower bounds, approximations, and ranges remain explicit. Approximate wire dates remain `on_or_about`, and date-only timestamps use midnight only as a storage convention. Unknown amounts/dates are null. Non-payment relations such as `co_founded` and `controlled` are not transfers. `summarized_multi_step_flow` does not imply a direct one-hop payment.

**Do not sum all edge amounts into losses or laundered totals.** Flows can overlap and have different meanings and currencies. Historical demo edges are excluded from `/graph`'s transaction summaries; sourced case metrics are available in `/demo/cases`. In particular, Liberty Reserve's total processed volume is distinguished from the range of admitted laundering, and HSBC's combined lower bound is not allocated to individual cartels.

All demo records carry case/dataset ids and remain `needs_review` so their case-specific limitations stay visible. Sources are linked rather than republished wholesale.
