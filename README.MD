# Dolos

**Veritas AI Hackathon · Finance**

**Team:** Abhi Agasanakoppa and Timothy Vu

Dolos turns financial statements into a directed transaction graph, ranks transfers for review, and shows the evidence behind each model decision.

[Try the demo](https://dolos-web.vercel.app) · [Source](https://github.com/abhirath-a/dolos) · [Notebook](dolos.ipynb) · [Mock statement pack](frontend/public/dolos-video-statements.zip) · [Technical guide](docs/technical-guide.md)

**Prototype only. Not financial advice, proof of money laundering, or a determination of criminal responsibility.**

## The problem

A spreadsheet shows individual payments. It is harder to see how money moves between connected accounts, which transfers deserve attention, and why a model flagged them. We built Dolos to bring those questions into one explorable graph.

## Try it as a judge

Choose an example, click **Run GraphSAGE**, and inspect a scored transfer. Open its model context to see the actual historical neighbors used. Switch to **Graph XGBoost**, run it, and inspect feature contributions and decision-tree branches.

| Live example | What to explore |
| --- | --- |
| The pass-through network | A transfer within a repeated routing pattern |
| Two senders, one collector | Incoming transfers converging on one account |
| Ordinary payment comparison | Eight generated payments used as a negative comparison |
| Routing and ordinary payments | Twelve transfers containing both kinds of generated activity |

Every click sends a new inference request. The server recalculates from transaction history and trained weights; it does not substitute recorded scores. Repeated runs on identical inputs should agree.

To test uploads, download the mock pack and upload its four PDFs together. They contain 1,200 synthetic transactions. Each PDF visibly discloses its embedded CSV export, which makes this test reproducible without an LLM call. Ordinary PDFs without that export use the configured extraction provider. [Video walkthrough and expected results](docs/video-demo.md)

## How it works

1. **Ingest:** upload PDF, CSV or JSON statements. A durable database queue lets a separate worker process batches.
2. **Connect:** accounts become nodes; transfers become directed edges. Records retain source-document and row/page provenance where available.
3. **Score:** directed GraphSAGE learns incoming and outgoing neighborhood representations. Graph-aware XGBoost combines transaction fields with historical account features. Both use history before the target hour to avoid future leakage.
4. **Explain:** GraphSAGE exposes sampled historical context. XGBoost exposes TreeSHAP contributions and actual tree paths. Context is not causal attribution, and raw model margins are not confidence percentages.

We expanded the notebook into an application with batch ingestion, flexible PDF extraction, durable jobs, an interactive graph, live model comparisons, explanations, and lightweight inference for deployment. React/TanStack Query power the frontend; FastAPI serves the API on Vercel; Railway runs the worker; Turso stores records/jobs; Cloudflare R2 stores documents.

## Results and model provenance

The notebook's **saved IBM AMLworld HI-Small benchmark** reports:

| Model | Average precision | Labeled cases in the top 100 alerts |
| --- | ---: | ---: |
| Transaction-only XGBoost | 0.1359 | 54 |
| Graph-aware XGBoost | 0.4755 | 98 |
| Directed GraphSAGE | 0.2428 | 83 |

These are saved notebook results, not a newly rerun benchmark or whole-dataset accuracy. Graph-aware XGBoost had the strongest recorded ranking.

**The website uses the same architectures with separate weights trained on our generated teaching data, not the IBM-trained weights.** In the mock pack's final-hour window, both models flagged four generated routing transfers and left eight ordinary transfers below the decision boundary. This checks the demo pipeline, not real-world effectiveness. [Reproducible validation](output/pdf/validation.json)

GraphSAGE's deployment runtime evaluates its trained tensors with NumPy. It matched the original PyTorch implementation within 0.000003 on the tested held-out demo rows, with identical decisions. XGBoost uses its native CPU runtime.

## Limitations and next steps

The teaching data is deliberately simple. It is not an independent external evaluation. Missing fields in ordinary PDF extractions can prevent trained-model scoring; those records use separately labeled pattern checks. Extraction can make mistakes, and named entities or highlighted transfers require human review.

Next steps are to deploy and evaluate the IBM-trained artifacts, test more statement layouts and datasets, measure false positives, and add access controls and retention policies before handling real statements. Use public or synthetic documents in this demo; the configured free extraction endpoint may log submitted data.

## Credits

- **Data:** [IBM AMLworld / Erik Altman](https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml) for the notebook benchmark. Website and mock-statement data are generated by [our fixture generator](dolos/model_demo.py); no real bank records are included.
- **Models:** [GraphSAGE / PyTorch Geometric](https://pytorch-geometric.readthedocs.io/en/latest/generated/torch_geometric.nn.conv.SAGEConv.html), [PyTorch](https://pytorch.org/), [XGBoost](https://xgboost.readthedocs.io/), and [scikit-learn](https://scikit-learn.org/). The demo AML weights are trained on our synthetic data, not externally pretrained AML weights.
- **PDF extraction:** [NVIDIA Nemotron 3.5 Super VL (free), through OpenRouter](https://openrouter.ai/nvidia/nemotron-3.5-super-vl:free). The configured/resolved extraction model is recorded with extracted relations; this model extracts transactions rather than deciding laundering risk.
- **AI assistance:** [OpenAI Codex](https://openai.com/codex/) and [ChatGPT](https://chatgpt.com/) assisted implementation, debugging, tests and documentation. [Impeccable](https://impeccable.style/) supplied interface-design guidance.
- **Application:** [React](https://react.dev/), [TanStack Query](https://tanstack.com/query), [React Flow](https://reactflow.dev/), [Dagre](https://github.com/dagrejs/dagre), [FastAPI](https://fastapi.tiangolo.com/), [NumPy](https://numpy.org/), [Polars](https://pola.rs/), [pypdf](https://pypdf.readthedocs.io/), and [ReportLab](https://www.reportlab.com/).

## Run locally

```sh
nix develop
uv sync --extra test --extra pdf-llm
pnpm --dir frontend install --frozen-lockfile
uv run uvicorn dolos.api:app --reload
```

In separate Nix shells, run `pnpm --dir frontend dev` and `uv run --extra pdf-llm dolos-worker`. Open `http://localhost:5173`. Built-in examples need no provider key; ordinary PDF extraction does. See the [technical guide](docs/technical-guide.md) for configuration and the [submission checklist](docs/submission-checklist.md) for the remaining form/video steps.
