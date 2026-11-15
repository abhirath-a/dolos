"""Source-linked investigation findings; never a determination of criminal liability."""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation


def analyze_graph(graph, historical=False):
    edges = graph['edges']
    flags = defaultdict(list)
    # Each rule has an explicit, inspectable rationale. Amounts alone never imply wrongdoing.
    outgoing, incoming = defaultdict(list), defaultdict(list)
    for edge in edges:
        outgoing[edge['source']].append(edge)
        incoming[edge['target']].append(edge)
        if historical:
            status = edge.get('evidence_status', '')
            if any(word in status for word in ('alleg', 'admitted', 'conviction', 'forfeiture', 'court')):
                flags[edge['id']].append({'rule': 'Source-linked case evidence',
                    'detail': edge.get('description') or status.replace('_', ' '), 'basis': status})
    if not historical:
        for node in incoming.keys() & outgoing.keys():
            for first in incoming[node]:
                for second in outgoing[node]:
                    if first['id'] == second['id'] or first['source'] == node or second['target'] == node:
                        continue
                    try:
                        first_time = datetime.fromisoformat(first.get('timestamp') or '')
                        second_time = datetime.fromisoformat(second.get('timestamp') or '')
                        elapsed = (second_time - first_time).total_seconds()
                        amount_in = Decimal(str(first.get('amount_received', first.get('amount'))))
                        amount_out = Decimal(str(second.get('amount_paid', second.get('amount'))))
                        currency_in = first.get('receiving_currency', first.get('currency'))
                        currency_out = second.get('payment_currency', second.get('currency'))
                        comparable = currency_in and currency_in == currency_out and amount_in > 0
                        if comparable and 0 <= elapsed <= 86400 and Decimal('.8') <= amount_out / amount_in <= Decimal('1.2'):
                            reason = {'rule': 'Rapid pass-through', 'detail':
                                'An incoming and outgoing transfer at this entity occur within 24 hours, in the same currency, with amounts within 20%. This does not establish a funding link.',
                                'related_edges': [first['id'], second['id']], 'entity_id': node}
                            for item in (first, second):
                                if reason not in flags[item['id']]:
                                    flags[item['id']].append(reason)
                            break  # One illustrative matching outgoing transfer per incoming transfer.
                    except (ValueError, TypeError, InvalidOperation):
                        continue
        # Two-way transfers are review signals only, not necessarily a laundering cycle.
        pairs = defaultdict(list)
        for edge in edges:
            pairs[(edge['source'], edge['target'])].append(edge)
        for (source, target), group in pairs.items():
            if source != target and (target, source) in pairs:
                for edge in group:
                    flags[edge['id']].append({'rule': 'Reciprocal transfers', 'detail':
                        'Transfers run in both directions between these entities in this graph. Timing and economic purpose require review.'})
    findings = [{'edge_id': edge['id'], 'source': edge['source'], 'target': edge['target'],
                 'reasons': flags[edge['id']]} for edge in edges if flags[edge['id']]]
    entities = defaultdict(int)
    for item in findings:
        for node in {item['source'], item['target']}:
            entities[node] += 1
    return {'method': 'historical_evidence' if historical else 'pattern_rules',
            'rule_scope': 'Rapid pass-through shows one matching outgoing transfer per incoming transfer, not an exhaustive flow decomposition.',
            'findings': findings, 'entities': [{'id': key, 'flagged_connections': value}
                for key, value in sorted(entities.items(), key=lambda item: -item[1])],
            'summary': ('Source-linked relationships highlighted for review.' if historical else
                'Pattern checks found relationships to review.' if findings else 'No configured pattern checks triggered in this graph.'),
            'limitation': 'Flags identify review candidates, not proof of laundering or responsibility. Unflagged relationships are not certified safe.'}
