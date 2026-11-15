# Dolos

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

React and TypeScript frontend. Use TanStack libraries where appropriate; the current app uses TanStack Query, React Flow, and Dagre. FastAPI serves the API, with a separate Python process consuming a durable SQL job queue. Cloudflare R2 stores uploaded documents; Turso/libSQL is the intended remote database.

Development uses a minimal Nix flake providing Node.js 26, pnpm, and uv. pnpm manages JavaScript dependencies; uv manages Python and its dependencies. Preserve the flake and dependency lockfiles. Do not replace this setup with globally installed Python or a separately downloaded Node runtime.

## Users

The confirmed immediate audience is visitors and judges evaluating Dolos at the Veritas AI hackathon. They need to understand the project quickly, explore a working example, and inspect the evidence behind the result.

The intended audience after the hackathon is undecided. Financial investigators and bank compliance teams have been discussed as possibilities, not selected as confirmed customers or users.

## Product Purpose

Turn batches of bank statements into an explorable graph of financial relationships. Let users run analysis, identify suspicious connections and involved entities, and inspect how the result was produced.

The user-requested journey is:

1. Open the site and choose a supplied example or upload a large batch of PDFs.
2. Generate and explore the directional relationship graph.
3. Run Analyze and see processing status while the selected method executes.
4. Inspect highlighted edges, connected entities, source evidence, and the explanation behind each finding.

A transfer from B to A is an edge from B to A. Entity involvement must not be presented as proof that the entity committed money laundering.

## Positioning

The demonstration connects document ingestion, a transaction graph, and inspectable analysis in one experience. Explainability is part of the core product: show actual tree-model decisions and the historical graph context used by GraphSAGE.

The user confirmed that the AI hackathon demonstration should emphasize the graph model. The approved redesign features a genuine model-ready transaction example, GraphSAGE as the primary demonstration, and XGBoost as a secondary comparison. Historical examples and uploads support that experience.

The project should demonstrate what actually ran. Source reconstruction, pattern rules, and trained-model inference are different methods and must remain distinguishable.

## Operating Context

Users can explore cited historical examples without submitting documents. Uploads are processed asynchronously, potentially across many batches, while users monitor status and review accepted or rejected records. They should be able to connect a finding back to a document, row, page, or public citation when available.

The hackathon also includes a recorded demonstration. The graph, analysis outcome, and explanation need to communicate clearly in that setting. The user prioritizes low operating cost and permits inexpensive model providers beyond OpenAI.

Vercel has been discussed for the frontend and API. A separately hosted or locally running worker, Turso, and R2 have been discussed for the remaining services. These are deployment intentions, not claims that cloud services are already provisioned.

## Capabilities and Constraints

- Inputs include PDF bank statements and canonical CSV/JSON transaction records. PDF extraction may use text, rendered pages, or direct-document processing depending on provider and configuration.
- Support large document selections through queued batch processing. Clearly disclose preview, analysis, and export scope; a bounded view is not an exhaustive investigation.
- XGBoost is an ensemble of decision trees. Explain compatible predictions with actual tree paths and feature contributions.
- GraphSAGE is a graph neural network. Show its sampled historical context separately from tree attributions; context membership is not causal attribution.
- The notebook models require complete canonical transaction fields and configured trained artifacts. Partial PDF relationships must not be silently padded with invented transaction fields.
- The user explicitly accepted trained-model inference where compatible, with clearly labeled pattern analysis for PDFs and historical cases where appropriate.
- Historical examples reconstruct relationships from cited public sources. They are not original bank statements, freshly inferred model predictions, or a complete account of a case's current legal status.
- Findings are review candidates. Raw model margins are not calibrated confidence percentages. Absence of a flag is not proof that a transaction is safe.
- Preserve missing information, evidence provenance, and uncertainty. Do not fabricate transfers, ownership, guilt, citations, model scores, performance claims, or deployment status.
- Uploaded statement contents may be sent to the configured extraction provider. Storage and processing explanations must accurately describe that behavior. Keep storage, database, and model-provider secrets server-side.

## Brand Commitments

The product name is Dolos. The user requested a polished React website, delegated library and implementation choices, and permitted backend changes to support the experience.

The user approved a full rebuild emphasizing the graph model, using the Evidence Desk direction described in `docs/design/dolos-redesign-brief.md`. Existing layout and styling may be replaced for that rebuild. The archived design document records the previous implementation, not the approved replacement brief.

## Evidence on Hand

- `dolos.ipynb`: original notebook.
- `dolos/model.py`, `dolos/inference.py`, and `dolos/train.py`: model training, serving, and explanation code.
- `dolos/migrations/0002_famous_cases.json`: six historical case graphs with public-source citations.
- `examples/`: sample statement inputs.
- `frontend/`: implemented Evidence Desk graph workspace, uploads, evidence inspector, and analysis interface.
- `dolos/model_demo.py` and `frontend/public/model-demo.json`: reproducible synthetic teaching fixture and actual recorded GraphSAGE/XGBoost outputs; matching local artifacts support live demonstration inference. This is separate from production model evaluation.
- `tests/` and frontend tests: automated pipeline and explanation checks. These are not proof of real-world detection accuracy.
- The saved critique under `.impeccable/critique/` records implementation findings, not approved product changes.

At initialization, live R2/Turso/provider integration and rendered browser QA have not been verified. No configured production ML artifact, independent accuracy study, customer testimonial, or commercial adoption evidence is established by this repository.

## Product Principles

1. Make the first demonstration useful before asking for sensitive documents or paid-service setup.
2. Follow money through directional relationships with inspectable evidence.
3. Explain the method that actually ran, including its eligibility and limitations.
4. Preserve user control and successful work during long batch operations.
5. Prefer low-cost operation without sacrificing provenance or inventing certainty.

## Open Decisions

- Primary audience and operational requirements after the hackathon.
- Production model artifact, calibration, review policy, and evaluation on real statement formats.
- Production hosting choices, authentication, document retention, and deletion behavior.
- Future landing-page mockup-first versus code-first preference. No standing workflow default has been selected or persisted.

## Context History

The previous product and design documents were moved intact to `docs/archive/impeccable-init-2026-11-15/` at the user's request. They are historical references, not competing active product briefs. This file is the active product record. Initialization does not redesign the application or approve the prior critique's fixes.
