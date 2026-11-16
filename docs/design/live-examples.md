# Live model examples

The main example picker now exposes only the synthetic pass-through network, plus uploaded investigations. The six historical reconstructions are excluded because they are cited relationship graphs, not canonical bank transactions with the complete amounts, currencies, accounts, and times required by the trained pipeline. The archival migrations and API records are retained intact.

Every example analysis posts to `/api/demo/model/analyze?model=graphsage` or `graph_xgboost`. Repeated clicks recompute server-side; changing models clears the previous selection/result. The frontend no longer substitutes bundled scores if live inference is unavailable. It shows the API error and offers Retry instead. The graph and data provenance still come from `model-demo.json`; the API requires matching live artifacts/source fingerprint.

The deployment must include the ML dependencies and compatible model artifacts. A static frontend build alone does not enable inference. Uploaded partial PDF relationships continue using explicitly labeled pattern checks when model eligibility is absent.

Checks cover historical selection migration, both live model endpoints, repeated computation, unavailable-live errors, and upload navigation preservation. Rendered browser verification remains outstanding because the browser host is unavailable in this environment.
