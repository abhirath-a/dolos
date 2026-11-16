# Narration draft

Read this script over the silent draft. Speak at a steady rate. Do not add a claim about real-world accuracy.

## 0:00–0:12

A payment table shows one transfer at a time. It does not show the full path of the money. Dolos puts these connections in a graph.

## 0:12–0:29

Dolos uses one model with two parts. GraphSAGE reads earlier transfers and makes number lists. These lists are called embeddings. XGBoost uses the embeddings and transaction fields to make the final decision.

## 0:29–0:46

The arrows show the direction of each transfer. The model uses history before the target hour. We can inspect the actual earlier transfers that GraphSAGE used. Later transfers do not enter this context.

## 0:46–1:08

Each result shows credit for the graph features and the direct transaction fields. The credits and the base value add to the final score. XGBoost also shows its tree decisions. These values explain the model. They do not prove a crime.

## 1:08–1:22

A useful test must include ordinary payments. In this generated comparison, none of the eight payments passes the model threshold. A transfer without a flag is not a guarantee of safety.

## 1:22–1:39

The test pack has four synthetic statements and twelve hundred transfers. Each statement includes a declared data export. A local test processed all four documents and scored four hundred thirty-two eligible transfers.

## 1:39–1:54

The notebook compares one baseline with the final hybrid. The website uses synthetic teaching data. Dolos is a prototype for human review. It is not financial advice or proof of money laundering.
