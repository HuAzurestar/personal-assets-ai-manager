"""Explicitly open collapsed advanced controls in real import UI scenarios."""


def open_import_advanced(page):
    panel = page.locator('[data-batch-advanced]')
    if not panel.evaluate('node => node.open'):
        panel.locator('summary').click()
