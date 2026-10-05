"""Actual native containers retain the original 24 Facts through explicit LINK."""
import base64
import csv
import io
from pathlib import Path
from types import SimpleNamespace
import zipfile

import openpyxl
import pytest
import xlwt
from sqlalchemy import select

from backend.entity import TransactionImportRow
from import_batch_helpers import confirm_api_batch
from test_pirc35_import_duplicate import manifest
from test_target_import import target_import_api  # noqa: F401


BASE = '/paam/import/v1'
CSV = Path(__file__).parent / 'fixtures/pirc35/ccb-2.csv'


def container(extension):
    output = io.BytesIO()
    rows = list(csv.reader(io.StringIO(CSV.read_text(encoding='utf8'))))
    if extension == 'zip':
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('Mock-statement.csv', CSV.read_bytes())
    else:
        book = openpyxl.Workbook() if extension == 'xlsx' else xlwt.Workbook()
        sheet = book.active if extension == 'xlsx' else book.add_sheet('Mock')
        for number, values in enumerate(rows):
            if extension == 'xlsx':
                sheet.append(values)
            else:
                for column, value in enumerate(values):
                    sheet.write(number, column, value)
        book.save(output)
    return output.getvalue()


def success(response):
    assert response.status_code == 200, response.text
    return response.json()['body']


def preview(client, filename, content):
    current = success(client.post(BASE + '/preview', json=dict(timezone='Asia/Shanghai', files=[
        dict(filename=filename, content_base64=base64.b64encode(content).decode())])))
    assert current['issue_count'] == 0, current
    route = BASE + '/preview/' + current['token']
    result = success(client.get(route + '/row/list', params=dict(preview_digest=current['preview_digest'], page_size=100)))
    assert result['total'] == len(result['items']) == 24
    return route, current, result['items']


@pytest.mark.parametrize('extension', ['xlsx', 'xls', 'zip'])
def test_native_same_content_requires_explicit_pair_and_adds_evidence_not_cash(target_import_api, extension):
    client, sessions, _engine = target_import_api
    def snapshot():
        with sessions() as db:
            return manifest(SimpleNamespace(db=db))
    _route, first, _rows = preview(client, 'Mock-original.csv', CSV.read_bytes())
    accepted = success(confirm_api_batch(client, first, explicit_new=True))
    ids = {row['transaction_id'] for row in accepted['processed_rows']}
    assert accepted['new_fact_count'] == len(ids) == 24
    original = snapshot()
    incoming = container(extension)
    route, current, rows = preview(client, 'Mock-same.' + extension, incoming)
    assert current['files'][0]['sha256'] != first['files'][0]['sha256']
    assert all(row['classification'] == 'NEW' and row['duplicate_hint']['state'] == 'SUSPECTED'
        and row['duplicate_hint']['candidate_count'] == 1 for row in rows)
    locators = [{key: row[key] for key in ('file_id', 'source_row_number')} for row in rows]
    choices = [row | dict(decision='ACCEPT') for row in locators]
    current = success(client.put(route, json=dict(expected_updated_time=current['updated_time'], choices=choices)))
    payload = dict(expected_updated_time=current['updated_time'], preview_digest=current['preview_digest'], selected_rows=locators)
    before = snapshot()
    blocked = success(client.post(route + '/confirm-preview', json=payload))
    assert not blocked['can_confirm'] and {item['code'] for item in blocked['issues']} == {'IMPORT_REVIEW_REQUIRED'}
    rejected = client.post(route + '/confirm', json=payload)
    assert rejected.status_code == 422 and rejected.json()['body']['code'] == 'IMPORT_REVIEW_REQUIRED'
    assert snapshot() == before
    current = success(client.get(route))
    pairs = success(client.post(route + '/pairing-preview', json=dict(expected_updated_time=current['updated_time'],
        preview_digest=current['preview_digest'], choices=choices, kind='SAME_SOURCE')))
    assert pairs['selected_count'] == len(pairs['items']) == 24
    assert all(item['state'] == 'SUGGESTED' and item['suggestion']['resolution'] == 'LINK_EXISTING'
        and item['suggestion']['target']['kind'] == 'FACT' for item in pairs['items'])
    assert {item['suggestion']['target']['transaction_id'] for item in pairs['items']} == ids
    assert snapshot() == before
    links = [item['row'] | dict(decision='ACCEPT', resolution='LINK_EXISTING', target=item['suggestion']['target'])
        for item in pairs['items']]
    current = success(client.put(route, json=dict(expected_updated_time=current['updated_time'], choices=links)))
    payload = dict(expected_updated_time=current['updated_time'], preview_digest=current['preview_digest'], selected_rows=locators)
    disclosure = success(client.post(route + '/confirm-preview', json=payload))
    assert disclosure['can_confirm']
    result = success(client.post(route + '/confirm', json=payload | dict(batch_preview_digest=disclosure['batch_preview_digest'])))
    assert result['new_fact_count'] == result['duplicate_fact_count'] == 0 and result['manual_linked_count'] == 24
    assert {row['transaction_id'] for row in result['processed_rows']} == ids
    assert all(row['resolution_effect'] == 'EVIDENCE_ONLY' and row['created_review_id'] == row['created_ledger_id'] == 0
        for row in result['processed_rows'])
    after = snapshot()
    for name in original.keys() - {'transaction_import_file', 'transaction_import_row'}:
        assert after[name] == original[name], name
    assert len(after['transaction_import_row']) == 48
    assert after['transaction_import_row'][:24] == original['transaction_import_row']
    with sessions() as db:
        assert all(row.row_status == 1 and row.transaction_fact_id in ids for row in db.scalars(select(TransactionImportRow)))
    # Retransmission is current accepted evidence, never a replayed cash write.
    _route, _current, again = preview(client, 'Mock-same.' + extension, incoming)
    assert all(row['classification'] == 'PROCESSED' for row in again)
    repeated = snapshot()
    for name in original.keys() - {'transaction_import_file'}:
        assert repeated[name] == after[name], name
