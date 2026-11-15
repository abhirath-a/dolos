"""PDF pipeline tests use synthetic PDF bytes and mocked provider responses; no paid calls."""
import base64
import io
import json

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter

from dolos.api import create_app
from dolos import pdf_extraction as pdf
from dolos.worker import process_one


def pdf_bytes(pages=1, text=None):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=612, height=792)
        if text:
            from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
            font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                                     NameObject('/Subtype'): NameObject('/Type1'),
                                     NameObject('/BaseFont'): NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
                DictionaryObject({NameObject('/F1'): font})})
            stream = DecodedStreamObject()
            stream.set_data(('BT /F1 10 Tf 40 740 Td (' + text + ') Tj ET').encode())
            page[NameObject('/Contents')] = stream
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def party(name, **kwargs):
    if isinstance(name, pdf.Party):
        return name
    return pdf.Party(name=name, bank=kwargs.get('bank'), account=kwargs.get('account'), kind='person')


def transfer(page=1, sender='Person B', receiver='Person A', **changes):
    return pdf.Transfer(**dict(dict(sender=party(sender), receiver=party(receiver),
        timestamp='2026-11-01', amount='125.50', currency='USD', description='Transfer to Person A',
        evidence='Nov 01 Transfer to Person A 125.50 debit', page=page,
        reference=None, confidence='high', review_reason=None), **changes))


class Client:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv('DOLOS_API_KEY', raising=False)
    monkeypatch.setenv('DOLOS_PDF_MODE', 'direct')
    monkeypatch.setenv('DOLOS_PDF_PROVIDER', 'openai')
    monkeypatch.setattr(pdf, '_client', lambda: Client())
    app = create_app(tmp_path)
    with TestClient(app) as client:
        yield client, app.state.store


def upload(client, data, filename='statement.pdf'):
    response = client.post('/batches', files={'files': (filename, data, 'application/pdf')})
    assert response.status_code == 202
    return response.json()


def result(transfers, warnings=None):
    return pdf.Extraction(transfers=transfers, warnings=warnings or [], complete=True)


def test_scanned_pdf_to_directional_graph_with_evidence(setup, monkeypatch):
    client, store = setup
    def extract(client, content, model):
        assert content[1]['type'] == 'input_file'
        data = base64.b64decode(content[1]['file_data'].split(',', 1)[1])
        assert len(PdfReader(io.BytesIO(data)).pages) == 1
        return result([transfer(), transfer(sender='Person C', receiver='Person B',
            evidence='Credit from Person C', amount=None, currency=None, timestamp=None)])
    monkeypatch.setattr(pdf, 'request_extraction', extract)
    batch = upload(client, pdf_bytes())
    assert process_one(store)
    graph = client.get('/graph').json()
    labels = {n['id']: n['label'] for n in graph['nodes']}
    assert [(labels[e['source']], labels[e['target']]) for e in graph['edges']] == [
        ('Person C', 'Person B'), ('Person B', 'Person A')]
    edge = next(e for e in graph['edges'] if e['amount'])
    assert edge['evidence'] == {'page': 1, 'quote': 'Nov 01 Transfer to Person A 125.50 debit'}
    assert edge['review_required']
    assert edge['extraction']['verification'] == 'unverified'
    assert len(client.get('/relations').json()['relations']) == 2
    assert client.get('/transactions').json()['transactions'] == []
    assert client.get('/batches/' + batch['id']).json()['status'] == 'needs_review'
    assert len(client.get('/graph', params={'account': edge['target']}).json()['edges']) == 1
    assert len(client.get('/graph', params={'before': '2026-11-02'}).json()['edges']) == 1


def test_page_chunking_and_no_context_duplicates(setup, monkeypatch):
    client, store = setup
    calls = []
    def extract(client, content, model):
        calls.append(content)
        return result([transfer(page=[1, 4, 7][len(calls) - 1])])
    monkeypatch.setattr(pdf, 'request_extraction', extract)
    upload(client, pdf_bytes(7))
    assert process_one(store)
    assert len(calls) == 3
    assert [len(PdfReader(io.BytesIO(base64.b64decode(c[1]['file_data'].split(',')[1]))).pages)
            for c in calls] == [4, 6, 3]
    assert sorted(e['evidence']['page'] for e in client.get('/graph').json()['edges']) == [1, 4, 7]
    page = client.get('/graph', params={'limit': 2}).json()
    assert page['has_more'] and page['next_offset'] == 2


def test_later_chunk_failure_rolls_back_whole_document(setup, monkeypatch):
    client, store = setup
    calls = []
    def extract(client, content, model):
        calls.append(True)
        if len(calls) == 2:
            raise pdf.DocumentError('Provider unavailable')
        return result([transfer()])
    monkeypatch.setattr(pdf, 'request_extraction', extract)
    batch = upload(client, pdf_bytes(4))
    assert process_one(store)
    assert client.get('/graph').json()['edges'] == []
    assert client.get('/batches/' + batch['id']).json()['documents'][0]['status'] == 'failed'


def test_out_of_target_page_fails_visibly(setup, monkeypatch):
    client, store = setup
    monkeypatch.setattr(pdf, 'request_extraction', lambda *args: result([transfer(page=9)]))
    batch = upload(client, pdf_bytes())
    process_one(store)
    doc = client.get('/batches/' + batch['id']).json()['documents'][0]
    assert doc['status'] == 'failed' and 'target pages' in doc['error']
    assert client.get('/graph').json()['edges'] == []


def test_missing_counterparty_is_rejected_but_valid_edges_survive(setup, monkeypatch):
    client, store = setup
    monkeypatch.setattr(pdf, 'request_extraction', lambda *args: result([
        transfer(), transfer(receiver=party(None))], warnings=['One entry was illegible']))
    batch = upload(client, pdf_bytes())
    process_one(store)
    doc = client.get('/batches/' + batch['id']).json()['documents'][0]
    assert (doc['accepted'], doc['rejected']) == (1, 2)
    errors = client.get('/documents/' + doc['id'] + '/errors').json()['errors']
    assert 'unidentified' in errors[0]['error']
    assert len(client.get('/graph').json()['edges']) == 1


def test_text_mode_and_scanned_fallback(setup, monkeypatch):
    client, store = setup
    monkeypatch.setenv('DOLOS_PDF_MODE', 'text')
    calls = []
    def extract(client, content, model):
        calls.append(content)
        return result([transfer()])
    monkeypatch.setattr(pdf, 'request_extraction', extract)
    upload(client, pdf_bytes(text='Owner Person B. Statement November 2026. Transfer to Person A USD 125.50 debit.'))
    process_one(store)
    assert calls[0][1]['type'] == 'input_text'
    upload(client, pdf_bytes())
    process_one(store)
    assert calls[1][1]['type'] == 'input_file'


def test_identity_resolution_and_nulls():
    named = pdf.party_node(party(' Person A '), 'doc1')
    assert named['id'] == pdf.party_node(party('person a'), 'doc2')['id']
    masked = party(None, account='****1234', bank='001')
    assert pdf.party_node(masked, 'doc1')['id'] != pdf.party_node(masked, 'doc2')['id']
    full = pdf.party_node(party('Person A', account='000123', bank='001'), 'doc1')
    assert full['id'] == '["001","000123"]'
    assert full['resolution_status'] == 'exact_identifier'
    assert pdf.party_node(party('Person A', account='****1234', bank='001'), 'doc2')['id'] == named['id']
    row = pdf.normalize_transfer(transfer(amount=None, timestamp=None, currency=None), 'doc', 'test', 'direct')
    assert row['timestamp'] is None and row['amount'] is None


def test_missing_key_fails_with_actionable_error(setup, monkeypatch):
    client, store = setup
    monkeypatch.undo()
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    monkeypatch.delenv('DOLOS_PDF_PROVIDER', raising=False)
    monkeypatch.delenv('DOLOS_API_KEY', raising=False)
    batch = upload(client, pdf_bytes())
    process_one(store)
    doc = client.get('/batches/' + batch['id']).json()['documents'][0]
    assert doc['status'] == 'failed'
    assert 'OPENROUTER_API_KEY' in doc['error']


def test_sdk_wire_format_and_refusal(monkeypatch):
    monkeypatch.setenv('DOLOS_PDF_PROVIDER', 'openai')
    openai = pytest.importorskip('openai')
    import httpx
    requests = []
    refuse = [False]
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        content = ({'type': 'refusal', 'refusal': 'Cannot extract'} if refuse[0] else
                   {'type': 'output_text', 'text': result([transfer()]).model_dump_json(), 'annotations': []})
        return httpx.Response(200, json={'id': 'resp_test', 'object': 'response', 'created_at': 0,
            'model': 'gpt-4.1', 'status': 'completed', 'output': [
                {'id': 'msg_test', 'type': 'message', 'role': 'assistant', 'status': 'completed', 'content': [content]}]})
    with openai.OpenAI(api_key='fake', http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
        response = pdf.request_extraction(client, [{'type': 'input_text', 'text': 'sample'}], 'gpt-4.1')
        assert response.transfers[0].sender.name == 'Person B'
        assert requests[0]['text']['format']['strict'] is True
        assert requests[0]['store'] is False
        refuse[0] = True
        with pytest.raises(pdf.DocumentError, match='refused or incomplete'):
            pdf.request_extraction(client, [], 'gpt-4.1')


def test_pdf_full_accounts_join_existing_canonical_graph(setup, monkeypatch):
    from pathlib import Path
    client, store = setup
    first = client.post('/batches', files={'files': ('canonical.csv', Path('examples/statement.csv').read_bytes())})
    assert first.status_code == 202 and process_one(store)
    monkeypatch.setattr(pdf, 'request_extraction', lambda *args: result([transfer(
        sender=party('Person B', bank='001', account='000123'),
        receiver=party('Person A', bank='002', account='000456'))]))
    batch = upload(client, pdf_bytes())
    assert process_one(store)
    graph = client.get('/graph').json()
    assert len(graph['nodes']) == 3 and len(graph['edges']) == 4
    assert any(n.get('name') == 'Person A' for n in graph['nodes'])
    assert len(client.get('/graph', params={'batch_id': batch['id']}).json()['edges']) == 1
    assert len(client.get('/transactions').json()['transactions']) == 3


def test_schema_invalid_amounts_and_incomplete_provider():
    for amount in ('NaN', '-1', '1e-99999999', '1,000'):
        with pytest.raises(ValueError):
            pdf.normalize_transfer(transfer(amount=amount), 'doc', 'test', 'direct')
    with pytest.raises(pdf.DocumentError, match='refused or incomplete'):
        pdf.request_extraction(SimpleResponseClient(status='incomplete'), [], 'test')
    with pytest.raises(pdf.DocumentError, match='could not read all'):
        pdf.request_extraction(SimpleResponseClient(complete=False), [], 'test')


class SimpleResponseClient:
    def __init__(self, status='completed', complete=True):
        from types import SimpleNamespace
        self.responses = SimpleNamespace(parse=lambda **kwargs: SimpleNamespace(
            status=status, output_parsed=pdf.Extraction(transfers=[], warnings=[], complete=complete)))


def test_free_router_defaults_use_own_key_and_endpoint(monkeypatch):
    monkeypatch.delenv('DOLOS_PDF_PROVIDER', raising=False)
    monkeypatch.delenv('DOLOS_PDF_MODEL', raising=False)
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    monkeypatch.setenv('OPENAI_API_KEY', 'unused-key')
    assert pdf.provider() == 'openrouter'
    assert pdf.default_model() == 'openrouter/free'
    with pytest.raises(pdf.DocumentError, match='OPENROUTER_API_KEY'):
        pdf._client()
    pytest.importorskip('openai')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'fake-router-key')
    with pdf._client() as client:
        assert str(client.base_url) == 'https://openrouter.ai/api/v1/'
        assert client.api_key == 'fake-router-key'


def test_free_router_scan_uses_local_images_and_records_resolved_model(setup, monkeypatch):
    pytest.importorskip('pypdfium2')
    from PIL import Image
    client, store = setup
    monkeypatch.setenv('DOLOS_PDF_PROVIDER', 'openrouter')
    monkeypatch.delenv('DOLOS_PDF_MODE', raising=False)  # Text-first default falls back for a scan.
    monkeypatch.delenv('DOLOS_PDF_MODEL', raising=False)
    def extract(client, content, model):
        assert model == 'openrouter/free'
        assert not any(p['type'] == 'input_file' for p in content)
        images = [p for p in content if p['type'] == 'input_image']
        assert len(images) == 1
        image = Image.open(io.BytesIO(base64.b64decode(images[0]['image_url'].split(',')[1])))
        assert image.format == 'PNG' and max(image.size) <= 2000
        output = result([transfer()])
        output._resolved_model = 'vendor/free-vision-model:free'
        return output
    monkeypatch.setattr(pdf, 'request_extraction', extract)
    upload(client, pdf_bytes())
    assert process_one(store)
    extraction = client.get('/graph').json()['edges'][0]['extraction']
    assert extraction['provider'] == 'openrouter'
    assert extraction['model'] == 'openrouter/free'
    assert extraction['resolved_model'] == 'vendor/free-vision-model:free'


def test_default_text_mode_avoids_vision_and_pdf_parser(setup, monkeypatch):
    client, store = setup
    monkeypatch.setenv('DOLOS_PDF_PROVIDER', 'openrouter')
    monkeypatch.delenv('DOLOS_PDF_MODE', raising=False)
    def extract(client, content, model):
        assert all(p['type'] == 'input_text' for p in content)
        return result([transfer()])
    monkeypatch.setattr(pdf, 'request_extraction', extract)
    upload(client, pdf_bytes(text='Owner Person B. Statement November 2026. Transfer to Person A USD 125.50 debit.'))
    assert process_one(store)
    assert client.get('/graph').json()['edges'][0]['extraction']['mode'] == 'text'


def test_openrouter_request_enforces_schema_price_routing_and_no_paid_ocr(monkeypatch):
    openai = pytest.importorskip('openai')
    import httpx
    monkeypatch.setenv('DOLOS_PDF_PROVIDER', 'openrouter')
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        assert request.url.host == 'openrouter.ai'
        return httpx.Response(200, json={'id': 'resp_test', 'object': 'response', 'created_at': 0,
            'model': 'qwen/free-example:free', 'status': 'completed', 'output': [
                {'id': 'msg_test', 'type': 'message', 'role': 'assistant', 'status': 'completed', 'content': [
                    {'type': 'output_text', 'text': result([transfer()]).model_dump_json(), 'annotations': []}]}]})
    with openai.OpenAI(api_key='fake', base_url='https://openrouter.ai/api/v1',
                      http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
        output = pdf.request_extraction(client, [{'type': 'input_text', 'text': 'Statement'}], 'openrouter/free')
    assert output._resolved_model == 'qwen/free-example:free'
    assert calls[0]['model'] == 'openrouter/free'
    assert calls[0]['provider'] == {'sort': 'price', 'require_parameters': True}
    assert calls[0]['text']['format']['strict'] is True
    assert 'plugins' not in calls[0] and 'models' not in calls[0]  # No OCR plugin or alternate model chain.


def test_quota_error_never_switches_to_paid_model(monkeypatch):
    openai = pytest.importorskip('openai')
    import httpx
    monkeypatch.setenv('DOLOS_PDF_PROVIDER', 'openrouter')
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(429, json={'error': {'message': 'Quota exhausted', 'code': 429}})
    with openai.OpenAI(api_key='fake', max_retries=0, base_url='https://openrouter.ai/api/v1',
                      http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
        with pytest.raises(pdf.DocumentError, match='no paid-model fallback'):
            pdf.request_extraction(client, [{'type': 'input_text', 'text': 'Statement'}], 'openrouter/free')
    assert len(calls) == 1 and calls[0]['model'] == 'openrouter/free'
