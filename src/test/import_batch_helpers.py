"""Explicit fictional-test choices through the same public bounded contract."""
from backend.schema.import_command import ImportConfirmInput, ImportReviseInput


def prepare_api_batch(client, preview, *, skip_invalid=False):
    token = preview["token"]
    preview = client.get(f"/paam/import/v1/preview/{token}").json()["body"]
    rows = []
    for page in range(1, 11):
        response = client.get(f"/paam/import/v1/preview/{token}/row/list", params=dict(
            preview_digest=preview["preview_digest"], page_index=page, page_size=100))
        assert response.status_code == 200, response.text
        body = response.json()["body"]
        rows.extend(body["items"])
        if page * 100 >= body["total"]:
            break
    assert 1 <= len(rows) <= 1000, "test helper requires an explicit bounded nonempty scope"
    selected = [{key: row[key] for key in ("file_id", "source_row_number")} for row in rows]
    response = client.put(f"/paam/import/v1/preview/{token}", json=dict(expected_updated_time=preview["updated_time"],
        choices=[identity | dict(decision="SKIP" if skip_invalid and row["classification"] in {"INVALID", "AMBIGUOUS"} else "ACCEPT")
                 for identity, row in zip(selected, rows)]))
    if response.status_code != 200:
        return response, None
    current = response.json()["body"]
    return None, dict(expected_updated_time=current["updated_time"], preview_digest=current["preview_digest"], selected_rows=selected)


def confirm_api_batch(client, preview, *, skip_invalid=False):
    error, payload = prepare_api_batch(client, preview, skip_invalid=skip_invalid)
    return error if error is not None else client.post(f"/paam/import/v1/preview/{preview['token']}/confirm", json=payload)


def prepare_service_batch(service, preview, *, skip_invalid=False):
    state = service.store.get(preview["token"])
    keys = sorted(state.rows)
    current = service.revise(preview["token"], ImportReviseInput(expected_updated_time=state.updated_time,
        choices=[dict(file_id=key[0], source_row_number=key[1], decision="SKIP" if skip_invalid and
                 state.candidates[key]["classification"] in {"INVALID", "AMBIGUOUS"} else "ACCEPT") for key in keys]))
    return ImportConfirmInput(expected_updated_time=current["updated_time"], preview_digest=current["preview_digest"],
        selected_rows=[dict(file_id=key[0], source_row_number=key[1]) for key in keys])


def confirm_service_batch(service, preview):
    return service.confirm(preview["token"], prepare_service_batch(service, preview))
