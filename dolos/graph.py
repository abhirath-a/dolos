from collections import defaultdict
from decimal import Decimal, localcontext


def build_graph(rows):
    with localcontext() as context:
        context.prec = 64
        return _build_graph(rows)


def _build_graph(rows):
    nodes = {}
    edges = []
    totals = defaultdict(lambda: [0, Decimal(0), Decimal(0)])
    relation_totals = defaultdict(lambda: [0, 0, Decimal(0)])
    for row in rows:
        if row.get('record_type') == 'relation':
            for side in ('sender', 'receiver'):
                node = row[side]
                if node['id'] not in nodes or not nodes[node['id']].get('name'):
                    nodes[node['id']] = node
            edges.append({'id': row['id'], 'source': row['sender_id'], 'target': row['receiver_id'],
                          'relation': row['relation'], 'timestamp': row['timestamp'],
                          'timestamp_precision': row.get('timestamp_precision'),
                          'amount': row['amount'], 'currency': row['currency'],
                          'description': row['description'], 'reference': row['reference'],
                          'evidence': row['evidence'], 'extraction': row['extraction'],
                          'review_required': row['review_required'], 'review_reasons': row['review_reasons'],
                          'document_id': row['document_id'], 'source_row': row['source_row']})
            # Public case graphs mix aggregate amounts and non-payment relationships.
            # Preserve their evidence semantics; do not turn them into transfer totals.
            if row.get('dataset_id'):
                for key in ('dataset_id', 'case_id', 'case_title', 'citations', 'evidence_status',
                            'amount_scope', 'amount_qualifier', 'date_qualifier', 'event_period'):
                    edges[-1][key] = row.get(key)
                continue
            total = relation_totals[(row['sender_id'], row['receiver_id'], row['currency'])]
            total[0] += 1
            if row['amount'] is not None and row['currency'] is not None:
                total[1] += 1
                total[2] += Decimal(row['amount'])
            continue
        for side, key in (('from', 'sender_id'), ('to', 'receiver_id')):
            nodes.setdefault(row[key], {'id': row[key], 'bank': row[f'{side}_bank'],
                                        'account': row[f'{side}_account'], 'label': row[f'{side}_account'],
                                        'kind': 'account', 'identity_basis': 'bank_account',
                                        'resolution_status': 'exact_identifier'})
        edges.append({'id': row['id'], 'source': row['sender_id'], 'target': row['receiver_id'],
                      'relation': 'transfer', 'timestamp': row['timestamp'], 'amount_paid': row['amount_paid'],
                      'payment_currency': row['payment_currency'],
                      'amount_received': row['amount_received'], 'receiving_currency': row['receiving_currency'],
                      'document_id': row['document_id'], 'source_row': row['source_row']})
        key = (row['sender_id'], row['receiver_id'], row['payment_currency'], row['receiving_currency'])
        total = totals[key]
        total[0] += 1
        total[1] += Decimal(row['amount_paid'])
        total[2] += Decimal(row['amount_received'])
    summary = [{'source': k[0], 'target': k[1], 'payment_currency': k[2], 'receiving_currency': k[3],
                'count': v[0], 'amount_paid': str(v[1]), 'amount_received': str(v[2])}
               for k, v in sorted(totals.items())]
    relations = [{'source': k[0], 'target': k[1], 'currency': k[2], 'count': v[0],
                  'amount_known_count': v[1], 'amount': str(v[2]) if v[1] else None}
                 for k, v in sorted(relation_totals.items(), key=lambda item: tuple(x or '' for x in item[0]))]
    return {'nodes': list(nodes.values()), 'edges': edges, 'summaries': summary, 'relation_summaries': relations}
