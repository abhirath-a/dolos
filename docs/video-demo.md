# Live model demo and video statements

## What changed

The model picker exposes only compatible synthetic transactions and uploaded investigations. Al Capone, 1MDB, HSBC, Liberty Reserve, OneCoin, and Bitfinex historical relationship graphs are removed from that picker: their cited claims do not supply the canonical transaction records required by the trained feature pipeline. Their archival migrations, citations and API records remain intact; inventing financial records for them would not be a legitimate model test.

Analyze recomputes the selected model on the server. It never replaces an unavailable live result with stored scores. Both GraphSAGE and Graph XGBoost remain available. Source CSV, manifest and real trained weights are packaged under `dolos/demo_assets/`, avoiding reliance on ignored local `var/` files.

## Deploy

Deploy the updated frontend, Vercel API and Railway worker. The API must have the `ml` extra installed and its package must fit the configured Vercel runtime. Packaging the small weights does not install PyTorch or remove Vercel bundle/memory limits. That hosted build remains to be verified.

For synthetic statement uploads, set this on the API:

```dotenv
DOLOS_MODEL_DIR=dolos/demo_assets/artifacts
```

This selects the synthetic teaching model. Use a separately validated trained artifact directory for other data. Keep API/worker on the same Turso/R2 configuration. The worker needs the new parser, but not ML dependencies, for the generated PDFs.

Check `/api/demo/model/status` through the frontend origin; `live_available` requires packaged artifacts and installed ML modules. Actual model calls remain the final runtime check.

## Video flow

1. Open the pass-through model example. Run GraphSAGE; inspect its actual sampled historical context. Switch to Graph XGBoost, run it, and inspect TreeSHAP contributions and actual tree branches.
2. Download `dolos-video-statements.zip` from the upload screen. Extract it locally.
3. Upload all four PDFs together in one investigation. Do not also upload the companion CSVs: that would duplicate the transactions.
4. Wait for four completed documents and 1,200 accepted transactions.
5. Set the graph's UTC start date/time to `2025-01-15 23:00`. Leave the end empty. The resulting 12 transfers make a readable video frame; all imported transactions remain available as model history.
6. Run GraphSAGE and inspect the highlighted routing transfers and model context. Then run Graph XGBoost and show its decision explanation. Selection and model runs are separate; changing models requires a fresh run.

## Measured result

`tools/verify_mock_statements.py` uploads the real PDF bytes to an isolated local API, runs the durable worker, reads the graph and calls both trained models through `/analyze`. No OpenRouter request is made for these PDFs.

- Four PDFs, 1,200 accepted transactions, 16 graph nodes, zero rejected rows.
- Both models score 432 eligible post-selection targets across all four documents. Earlier records supply historical context.
- In the final-hour window, both models score all 12 transfers: four positive model decisions and eight non-positive decisions. Those match the synthetic routing/ordinary motifs in this particular fixture.
- GraphSAGE returns actual sampled-neighbor context; Graph XGBoost returns TreeSHAP and tree paths.
- Exact observed margins and run ID are recorded in `output/pdf/validation.json` and included in the download archive.

These results validate plumbing and an intentionally simple teaching fixture, not real-world accuracy, calibrated confidence, or criminal responsibility. Models were not retrained on the uploaded PDFs. The data comes from the same synthetic generator as the teaching model and is not an independent external evaluation.

## PDF extraction disclosure

Every generated PDF visibly declares its synthetic origin and includes an exact `dolos-transactions.csv` attachment. The parser validates that canonical export, retaining account strings, decimal amounts and timestamps. Graph evidence records attachment name and row. It does not independently reconcile the rendered page with the embedded CSV. This makes the video reproducible without claiming to demonstrate OCR/LLM extraction.

Ordinary bank PDFs without that attachment retain the flexible provider extraction path. Partial extracted relations still use labeled pattern checks; they are not silently converted into complete model inputs. Live third-party extraction and deployed R2/Turso behavior were not tested by this local validation.

## Reproduce

```sh
nix develop
uv run --with reportlab --extra pdf-llm python tools/generate_mock_statements.py
uv run --extra ml --extra test python tools/verify_mock_statements.py
```

The second command rebuilds the validation report and public ZIP. To regenerate the trained synthetic model and its matching source, use `uv run --extra ml python -m dolos.model_demo` first, then recreate/revalidate the statement pack.
