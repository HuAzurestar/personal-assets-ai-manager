"""Shared actual-browser list readability checks; no financial writes."""


def assert_list_readability(page, row, primary, secondary, *, amount=None, max_height=100):
    def size(locator):
        return locator.evaluate("node => parseFloat(getComputedStyle(node).fontSize)")

    measured = {"primary": size(primary), "secondary": size(secondary)}
    if amount is not None:
        measured["amount"] = size(amount)
    assert measured["primary"] == 14, measured
    assert measured["secondary"] == 12, measured
    if amount is not None:
        assert measured["amount"] == 14, measured
    if page.viewport_size["width"] >= 1100:
        assert row.bounding_box()["height"] <= max_height, row.bounding_box()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    return measured


def assert_full_list_text(locator, expected_text):
    """Check real long text is laid out, not hidden by a one-line ellipsis."""
    assert expected_text in locator.inner_text()
    layout = locator.evaluate("""node => {
        const style = getComputedStyle(node), box = node.getBoundingClientRect();
        return {whiteSpace: style.whiteSpace, overflow: style.overflow,
            height: box.height, width: box.width,
            clientHeight: node.clientHeight, scrollHeight: node.scrollHeight};
    }""")
    assert layout['height'] > 0 and layout['width'] > 0, layout
    assert layout['whiteSpace'] not in ('nowrap', 'pre'), layout
    assert layout['overflow'] not in ('hidden', 'clip'), layout
    assert layout['scrollHeight'] <= layout['clientHeight'] + 1, layout
