"""Offline training of a GraphSAGE encoder -> XGBoost final classifier.

Run: nix develop --command uv run --extra ml python tools/train_hybrid.py
No GraphSAGE retraining, holdout tuning, or serving-time training is performed.
"""
import argparse
import json
from pathlib import Path
from dolos.hybrid import train_hybrid

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', type=Path, default=ROOT / 'dolos/demo_assets/artifacts')
    parser.add_argument('--source', type=Path, default=ROOT / 'dolos/demo_assets/synthetic-transfers.csv')
    args = parser.parse_args()
    print(json.dumps(train_hybrid(args.artifacts, args.source), indent=2))


if __name__ == '__main__':
    main()
