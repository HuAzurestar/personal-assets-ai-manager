"""Shared actual-browser list readability checks; no financial writes."""


def assert_list_readability(page, row, primary, secondary, *, amount=None, max_height=100):
    def size(locator):
        return locator.evaluate("node => parseFloat(getComputedStyle(node).fontSize)")

    measured = {"primary": size(primary), "secondary": size(secondary)}
    if amount is not None:
        measured["amount"] = size(amount)
    assert measured["primary"] >= 14, measured
    assert measured["secondary"] >= 12, measured
    if amount is not None:
        assert measured["amount"] >= 14, measured
    if page.viewport_size["width"] >= 1100:
        assert row.bounding_box()["height"] <= max_height, row.bounding_box()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    return measured
