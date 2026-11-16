# Dolos demo voiceover

**1 minute 54 seconds · Veritas AI Finance track**

Record each paragraph at the start of its time range. Leave any time after the paragraph quiet. The video is silent so you can record your own narration.

## 0:00–0:15 · Pass-through network

When money moves through several accounts, each transfer can look ordinary on its own. Dolos puts those transfers on a graph so a reviewer can follow the money. Here’s a simulated pass-through network.

## 0:15–0:31 · Analyze network

I’ll run the model on the public site. GraphSAGE summarizes the earlier transfers around each account. XGBoost combines those summaries with the transaction fields and produces one score. This run flags one connection for review.

## 0:31–0:49 · Explain the score

Here’s the flagged transfer. Its score is about seven point six. The score starts at a base value, then the neighborhood and transaction fields each move it up or down. Here, the neighborhood contributes most.

## 0:49–1:05 · Inspect model context and tree paths

I can isolate the earlier transfers that GraphSAGE sampled and check where that history ends. I can also open the XGBoost tree paths and see which branches this transfer took.

## 1:05–1:20 · Ordinary payments

Now I’ll run the same model on eight generated ordinary payments. None crosses the threshold in this example. Both networks come from synthetic data, so these results tell us how the demo behaves on these examples.

## 1:20–1:38 · Upload statements

I’ll add four synthetic bank statements in one upload. Each PDF has an embedded transaction export. Dolos queues the files and shows the processing status for each document.

## 1:38–1:54 · Review the investigation

The site accepted all four uploads. Processing was still underway when I opened the investigation. The graph will populate as the worker imports the transactions. Dolos is a prototype for human review. It isn’t financial advice or proof of wrongdoing.
