"""Load cited public-history case graphs without LLM calls or fabricated bank records."""
import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path

from .parsers import timestamp

DATASET_PATH = Path(__file__).with_name('migrations') / '0002_famous_cases.json'


def load_dataset():
    dataset = json.loads(DATASET_PATH.read_text(encoding='utf-8'))
    sources = {source['id']: source for source in dataset['sources']}
    case_ids = set()
    node_ids = set()
    edge_ids = set()
    for case in dataset['cases']:
        if case['id'] in case_ids:
            raise ValueError('Duplicate case id')
        case_ids.add(case['id'])
        local_nodes = {node['id'] for node in case['nodes']}
        if len(local_nodes) != len(case['nodes']) or local_nodes & node_ids:
            raise ValueError('Duplicate node id')
        node_ids.update(local_nodes)
        for edge in case['edges']:
            if edge['id'] in edge_ids or edge['source'] not in local_nodes or edge['target'] not in local_nodes:
                raise ValueError('Invalid edge identifiers')
            edge_ids.add(edge['id'])
            if not edge['citations'] or not edge['evidence_status']:
                raise ValueError('Every edge needs citations and evidence status')
            for citation in edge['citations']:
                if citation['source_id'] not in sources or not citation['locator']:
                    raise ValueError('Citation does not resolve')
            if edge['amount'] is not None:
                value = Decimal(edge['amount'])
                if not value.is_finite() or value < 0 or not edge['currency'] or not edge['amount_qualifier']:
                    raise ValueError('Invalid cited amount')
    return dataset


def case_rows(dataset, case):
    sources = {source['id']: source for source in dataset['sources']}
    nodes = {node['id']: dict(node, name=node['label'], bank=None, account=None,
                              identity_basis='curated_case_entity', resolution_status='source_described',
                              case_id=case['id']) for node in case['nodes']}
    for edge in case['edges']:
        citations = [dict(citation, url=sources[citation['source_id']]['url'],
                          published=sources[citation['source_id']]['published']) for citation in edge['citations']]
        yield dict(record_type='relation', relation=edge['relation'], case_id=case['id'],
                   case_title=case['title'], dataset_id=dataset['dataset_id'],
                   sender_id=edge['source'], receiver_id=edge['target'],
                   sender=nodes[edge['source']], receiver=nodes[edge['target']],
                   timestamp=timestamp(edge['event_date']) if edge['event_date'] else None,
                   timestamp_precision='date' if edge['event_date'] else None,
                   date_qualifier=edge['date_qualifier'], event_period=case['period'],
                   amount=edge['amount'], currency=edge['currency'], amount_scope=edge['amount_scope'],
                   amount_qualifier=edge['amount_qualifier'], description=edge['description'],
                   reference=edge['id'], evidence_status=edge['evidence_status'], citations=citations,
                   evidence={'page': None, 'quote': None, 'kind': 'source_paraphrase', 'citations': citations},
                   extraction={'provider': 'curated_public_sources', 'model': None,
                               'mode': 'historical_demo', 'verification': 'source_linked_not_independently_adjudicated'},
                   review_required=True, review_reasons=[case['caveat']])


def insert_cases(db):
    """Data migration body. Caller owns the transaction and migration ledger."""
    import time
    dataset = load_dataset()
    fingerprint = hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest()
    for case in dataset['cases']:
        batch_id = 'demo-' + fingerprint[:12] + '-' + case['id']
        document_id = batch_id + '-source-graph'
        rows = list(case_rows(dataset, case))
        db.execute('INSERT OR IGNORE INTO batches VALUES(?,?)', (batch_id, time.time()))
        digest = hashlib.sha256((fingerprint + ':' + case['id']).encode()).hexdigest()
        db.execute("""INSERT OR IGNORE INTO documents
            (id,batch_id,filename,sha256,suffix,status,accepted,rejected)
            VALUES(?,?,?,?,?,'needs_review',?,0)""",
                   (document_id, batch_id, case['id'] + '.curated-case.json', digest, '.case', len(rows)))
        db.execute('INSERT OR IGNORE INTO batch_documents VALUES(?,?)', (batch_id, document_id))
        for index, row in enumerate(rows, 1):
            db.execute('INSERT OR IGNORE INTO relations VALUES(?,?,?,?,?,?,?)',
                       (document_id + ':' + str(index), document_id, index, row['timestamp'],
                        row['sender_id'], row['receiver_id'], json.dumps(row)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', help='Uploaded-file directory; database uses DOLOS_DATABASE_URL')
    parser.add_argument('--database-url', help='Local file: or remote libsql: database URL')
    parser.add_argument('--list', action='store_true', help='List bundled cases without writing a database')
    args = parser.parse_args()
    if args.list:
        dataset = load_dataset()
        print(json.dumps([{k: c[k] for k in ('id', 'title', 'hook')} for c in dataset['cases']], indent=2))
    else:
        from .migrate import migrate
        print(json.dumps(migrate(args.data_dir, args.database_url), indent=2))


if __name__ == '__main__':
    main()
