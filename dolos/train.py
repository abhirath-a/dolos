import argparse


def main():
    parser = argparse.ArgumentParser(description='Run notebook benchmark and export serving artifacts')
    parser.add_argument('csv')
    parser.add_argument('directory')
    parser.add_argument('--epochs', type=int, default=6)
    parser.add_argument('--batch-size', type=int, default=384)
    parser.add_argument('--scope-rows', type=int)
    args = parser.parse_args()
    from .model import run
    run(args.csv, args.directory, epochs=args.epochs, batch_size=args.batch_size, scope_rows=args.scope_rows)


if __name__ == '__main__':
    main()
