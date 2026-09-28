"""Offline browser regression for automatic and command refresh presentation state."""
import os
import json
from pathlib import Path
import socket
import tempfile
import threading
import time

import httpx
from playwright.sync_api import expect, sync_playwright
import uvicorn

from serve_m2_ui import prepare_app


def run():
    with tempfile.TemporaryDirectory(prefix='paam-refresh-') as temp:
        app = prepare_app(Path(temp))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        try:
            for _ in range(100):
                try:
                    if httpx.get(base + '/api/health').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            with sync_playwright() as p:
                browser = p.chromium.launch(channel='msedge' if os.name == 'nt' else None, headless=True)
                page = browser.new_page(viewport={'width':390, 'height':844})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(base + '/#details/auto-rule')
                row = page.locator('[data-rule-row]').first
                expect(row).to_be_visible()
                help_panel = page.locator('details[data-preserve="schedule-help"]')
                help_panel.locator('summary').click()
                page.evaluate('window.originalRule = document.querySelector("[data-rule-row]")')
                top = row.bounding_box()['y']
                page.wait_for_timeout(11000)
                assert row.evaluate('(e) => e === window.originalRule')
                assert help_panel.evaluate('(e) => e.open')
                assert abs(row.bounding_box()['y'] - top) <= 2
                row.locator('[data-action="rule-edit"]').click()
                page.locator('[data-form="automation-rule"] [name="name"]').fill('Refresh regression')
                page.locator('[data-form="automation-rule"] button[type="submit"]').click()
                expect(page.locator('dialog[open]')).to_have_count(0)
                expect(row).to_contain_text('Refresh regression')
                assert help_panel.evaluate('(e) => e.open')
                rule_id = row.get_attribute('data-rule-row')
                page.goto(base + f'/#details/auto-rule?rule_id={rule_id}')
                stats = page.locator('details[data-preserve="rule-statistics"]')
                stats.locator('summary').click()
                page.wait_for_timeout(5500)
                assert stats.evaluate('(e) => e.open')

                # Non-automation pages use the same policy for periodic and command
                # refreshes, without jumping to the top or interrupting inline drafts.
                view_reads = []

                def many_views(route):
                    response = route.fetch()
                    envelope = response.json()
                    body = envelope['body']
                    example = body['items'][0]
                    before = [dict(example, id=1000+i, name=f'Before {i}', tags=[]) for i in range(10)]
                    after = [dict(example, id=2000+i, name=f'After {i}', tags=[]) for i in range(10)]
                    body['items'] = before + body['items'] + after
                    body['total'] = len(body['items'])
                    view_reads.append(1)
                    route.fulfill(response=response, body=json.dumps(envelope))

                page.route('**/paam/tag/v1/view/list?*', many_views)
                page.set_viewport_size({'width':1440, 'height':900})
                page.goto(base + '/#details/tag')
                card = page.locator('[data-view-card="1"]')
                expect(card).to_be_visible()
                card.evaluate('(e) => e.scrollIntoView({block:"center"})')
                control = card.locator('[data-action="view-status"]')
                control.focus()
                top = card.bounding_box()['y']
                reads = len(view_reads)
                page.wait_for_timeout(5500)
                assert len(view_reads) > reads
                assert abs(card.bounding_box()['y'] - top) <= 2
                expect(control).to_be_focused()
                control.click()
                expect(control).to_have_attribute('data-status', 'ACTIVE')
                assert abs(card.bounding_box()['y'] - top) <= 2
                draft = page.locator('[data-view-card="2"]')
                draft.locator('[data-action="new-tag"]').click()
                field = draft.locator('[name="name"]')
                field.fill('unfinished draft')
                field.evaluate('(e) => { e.setSelectionRange(2,8); window.draftField=e; }')
                page.wait_for_timeout(5500)
                assert field.evaluate('(e) => e === window.draftField && e.selectionStart === 2 && e.selectionEnd === 8')
                expect(field).to_have_value('unfinished draft')
                page.unroute('**/paam/tag/v1/view/list?*')

                # Shared state preservation: insertion above the viewport, caret,
                # nested scrolling, stable identity, removal focus and disabled state.
                page.goto(base + '/api/health')
                result = page.evaluate('''async () => {
                  const {preserveView, patchMarkup} = await import('/static/js/util/view_state.js');
                  document.body.innerHTML = '<main id="test"></main>';
                  const root = document.querySelector('#test');
                  const row = (i) => `<article data-fact-row="${i}" style="height:100px"><button data-action="edit" data-id="${i}">edit</button><input name="note" value="server"><details><summary>Statistics</summary><span>count</span></details></article>`;
                  const markup = Array.from({length:30}, (_,i) => row(i+1)).join('');
                  root.innerHTML = markup;
                  const original = root.querySelector('[data-fact-row="8"]');
                  const field = original.querySelector('input');
                  field.value = 'draft text'; field.focus({preventScroll:true}); field.setSelectionRange(2,7);
                  original.querySelector('details').open = true;
                  window.scrollTo(0,700);
                  const before = original.getBoundingClientRect().top;
                  preserveView(root, () => patchMarkup(root, row(0) + markup));
                  const stable = original === root.querySelector('[data-fact-row="8"]') && original.querySelector('details').open;
                  const caret = document.activeElement === field && field.value === 'draft text' && field.selectionStart === 2 && field.selectionEnd === 7;
                  const anchor = Math.abs(before-original.getBoundingClientRect().top) <= 1;
                  root.querySelector('[data-fact-row="8"] button').focus({preventScroll:true});
                  preserveView(root, () => patchMarkup(root, row(0) + markup.replace(row(8), '')));
                  const fallback = document.activeElement.closest('[data-fact-row]')?.dataset.factRow === '9';
                  root.innerHTML = '<div id="scroller" style="height:100px;overflow:auto"><div style="height:900px"><input name="selected" type="checkbox" value="1"></div></div>';
                  const scroller = root.firstElementChild;
                  scroller.scrollTop = 150; root.querySelector('input').checked = true;
                  preserveView(root, () => {root.innerHTML = root.innerHTML.replace('type="checkbox"', 'disabled type="checkbox"');});
                  const nested = root.firstElementChild.scrollTop === 150 && !root.querySelector('input').checked;
                  return {stable,caret,anchor,fallback,nested};
                }''')
                assert all(result.values()), result
                assert not errors, errors
                browser.close()
            print('PASS automatic and command refresh in rules and tags, keyed identity, expansion, inline draft, caret, viewport anchor, removed-row focus, nested scroll, authoritative disabled state')
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            from backend.core import target_database
            target_database.engine.dispose()


if __name__ == '__main__':
    run()
