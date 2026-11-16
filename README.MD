# Dolos

**Track:** AI + Finance

**Team:** Abhirath Agasanakoppa and Timothy Vu

[Live demo](https://dolos-web.vercel.app) · [Source code](https://github.com/abhirath-a/dolos) · [Evaluation notebook](dolos.ipynb) · [Sample statements](https://drive.google.com/drive/folders/1_wOnd3X4I4Ws3uKO-V3OOrPi05OkOCEL?usp=drive_link) · [Video](https://youtu.be/HR9kh6V0AYs)

**Dolos is a prototype for human review. Its scores do not constitute financial advice or evidence of wrongdoing.**

## The problem

When a small business's finance analyst reviews a set of statements for unusual transfers, a ledger shows each payment on its own. The analyst has to piece together which accounts repeatedly pass money to one another and decide which transfers to review first.

## What we built

Dolos takes PDF, CSV, or JSON statements and turns them into a directed graph of accounts and transfers. It ranks eligible transfers for review and lets the analyst inspect the account history the model used, see what raised or lowered the score, and return to the source record.

In the [live demo](https://dolos-web.vercel.app), choose **The pass-through network**, click **Analyze network**, and select the flagged transfer. To try ingestion, upload the four [sample PDFs](frontend/public/dolos-video-statements.zip) together. Their embedded CSV exports supply 1,200 synthetic transfers without an extraction API call.

## What we added

Our own idea. We asked whether an account's incoming and outgoing payment history would improve transfer ranking. We built the graph view so a reviewer could inspect that history and the evidence behind a score in the same workflow.

## How it works

We generated 2,880 labeled transfers with simulated routing patterns and ordinary payments. We validated the CSV rows, sorted them by time, and learned category codes on the training set. The chronological split used 2,016 training, 432 validation, and 432 test transfers. Each prediction uses graph history from before the target hour.

The baseline is XGBoost with transaction fields such as amount, currency, and payment format. The final model adds GraphSAGE embeddings that summarize both accounts' earlier incoming and outgoing transfers. We froze the encoder after validation and trained one XGBoost classifier on the combined features. TreeSHAP shows how the graph and transaction feature groups contributed to the final score.

## Results

Both models scored the same 432 held-out synthetic transfers, including 144 labeled routing transfers.

| Model | Average precision | Precision at 100 |
| --- | ---: | ---: |
| Transaction-only XGBoost baseline | 0.976999 | 0.980 |
| GraphSAGE → XGBoost | 1.000000 | 1.000 |

The baseline flagged an ordinary **$9,243 wire** from account `0016` to `0007`. Its amount and payment format resembled generated routing transfers, which may explain the false alert. The hybrid correctly left it unflagged and made no classification errors on this split. Because the training and test data came from the same generator, the scores tell us how well the model recognizes these simulated patterns. The live demo also uses weights trained on synthetic data. On regular PDFs, missing fields such as payment format can prevent model scoring. Dolos labels any fallback pattern checks separately.

## What we'd do next

- Run the same comparison on IBM AMLworld and measure false alerts and missed transfers on its held-out data.
- Test extraction on more statement layouts and measure how often missing or incorrect fields prevent reliable scoring.

## Sources

- **Data:** [Our synthetic data generator](dolos/model_demo.py) and [IBM AMLworld](https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml), an optional notebook dataset.
- **Models and evaluation:** [GraphSAGE](https://arxiv.org/abs/1706.02216), [PyTorch](https://pytorch.org/), [PyTorch Geometric](https://pytorch-geometric.readthedocs.io/), [XGBoost](https://xgboost.readthedocs.io/), [scikit-learn](https://scikit-learn.org/), [NumPy](https://numpy.org/), and [Polars](https://pola.rs/).
- **AI tools:** We used [OpenAI Codex](https://openai.com/codex/) a little for UI design, with guidance from [Impeccable](https://impeccable.style/). Codex and [ChatGPT](https://chatgpt.com/) also assisted with implementation, debugging, tests, and documentation. [NVIDIA Nemotron via OpenRouter](https://openrouter.ai/nvidia/nemotron-3.5-super-vl:free) extracts transaction fields from PDFs that lack an embedded export.
- **App and documents:** [React](https://react.dev/), [TanStack Query](https://tanstack.com/query), [React Flow](https://reactflow.dev/), [Dagre](https://github.com/dagrejs/dagre), [FastAPI](https://fastapi.tiangolo.com/), [pypdf](https://pypdf.readthedocs.io/), and [ReportLab](https://www.reportlab.com/).
