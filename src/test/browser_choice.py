"""Exercise named draft choices through the same visible controls as users."""
import json
import os
from pathlib import Path

from playwright.sync_api import expect
from browser_artifact import viewport_evidence


def choose_local(page, host, name, key):
    host.locator(f'[data-named-choice="{name}"] [data-choice-pick]').click()
    page.locator(f'dialog[open] [data-local-picker] [data-local-id="{key}"]').click()


def verify_local_choice_capacity(page):
    """Projection capacity, not a claim that 1,000 financial groups publish."""
    api_requests = []
    def record_api(request):
        if '/paam/' in request.url:
            api_requests.append(request.url)
    page.on('request', record_api)
    measured = page.evaluate("""async () => {
        const {namedChoice, bindLocalChoice} = await import('/static/js/component/workbench.js');
        const {localChoiceMap} = await import('/static/js/component/local-choice.js');
        const choices = localChoiceMap(Array.from({length:1000}, (_, i) =>
            [`cash-${i}`, `Mock draft ${String(i).padStart(4, '0')} · CNY`]));
        const controller = new AbortController(), host = document.createElement('section');
        host.dataset.localCapacity = '';
        document.body.append(host);
        const started = performance.now();
        host.innerHTML = Array.from({length:1000}, (_, i) =>
            namedChoice(`choice_${i}`, `Mock draft consumer ${i}`)).join('');
        for (let i=0; i<1000; i++) bindLocalChoice(host, `choice_${i}`, {
            title:'Mock local draft capacity', choices:() => choices, signal:controller.signal});
        window.__localChoiceFixture = {choices, controller, host};
        return {candidates:choices.size, consumers:host.querySelectorAll('[data-named-choice]').length,
            nodes:host.querySelectorAll('*').length, options:host.querySelectorAll('option').length,
            markup_bytes:new TextEncoder().encode(host.innerHTML).length,
            mount_ms:Math.round(performance.now()-started)};
    }""")
    assert measured['candidates'] == measured['consumers'] == 1000, measured
    assert measured['options'] == 0 and measured['nodes'] <= 12000, measured
    host = page.locator('[data-local-capacity]')
    first = host.locator('[data-named-choice="choice_0"]')
    first.locator('[data-choice-pick]').click()
    picker = page.locator('dialog[open] [data-local-picker]')
    expect(picker.locator('[data-local-id]')).to_have_count(20)
    expect(picker.locator('[data-local-count]')).to_contain_text('共 1000 项')
    picker.locator('[data-local-next]').click()
    expect(picker.locator('[data-local-count]')).to_contain_text('第 2 / 50 页')
    expect(picker.locator('[data-local-id]')).to_have_count(20)
    picker.locator('[data-local-word]').fill('mock draft 0999')
    expect(picker.locator('[data-local-id]')).to_have_count(1)
    viewport_evidence(page, 'fix-r09-bounded-local-picker')
    picker.locator('[data-local-id="cash-999"]').click()
    expect(first.locator('input')).to_have_value('cash-999')
    expect(first.locator('[data-choice-label]')).to_contain_text('Mock draft 0999')
    # Mutate the live map after rendering: a stale visible button is not a
    # licence to select a removed draft. No HTTP or financial mutation here.
    second = host.locator('[data-named-choice="choice_1"]')
    second.locator('[data-choice-pick]').click()
    picker.locator('[data-local-word]').fill('mock draft 0998')
    page.evaluate("window.__localChoiceFixture.choices.delete('cash-998')")
    picker.locator('[data-local-id="cash-998"]').click()
    expect(picker.locator('[role=status]')).to_contain_text('对象已移除')
    expect(picker.locator('[data-local-id]')).to_have_count(0)
    expect(second.locator('input')).to_have_value('')
    page.evaluate('window.__localChoiceFixture.controller.abort()')
    expect(page.locator('dialog[open] [data-local-picker]')).to_have_count(0)
    page.evaluate('window.__localChoiceFixture.host.remove(); delete window.__localChoiceFixture')
    page.remove_listener('request', record_api)
    assert not api_requests, api_requests
    measured.update(max_rendered_candidates=20, api_requests=0)
    destination = os.getenv('PIRC35_BROWSER_EVIDENCE_DIR')
    if destination:
        (Path(destination) / 'fix-r09-local-capacity.json').write_text(
            json.dumps(measured, sort_keys=True), encoding='utf-8')
    print('LOCAL_CHOICE_CAPACITY ' + json.dumps(measured, sort_keys=True))
