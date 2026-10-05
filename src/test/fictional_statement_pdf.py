"""Tiny text PDF fixture with fictional values; no original bank bytes/fonts."""


def statement_pdf(provider="abc", pages=2):
    labels = ["交易日期", "交易时间", "交易摘要", "交易金额", "本次余额", "对手信息", "日志号", "交易渠道", "交易附言"] if provider == "abc" else [
        "记账日期", "货币", "交易金额", "联机余额", "交易摘要", "对手信息", "客户摘要"]
    bank = "农业银行" if provider == "abc" else "招商银行"
    texts = []
    for index in range(pages):
        values = [f"202401{index % 28 + 1:02}", "120000", "Mock", "-10.25", "1000.00", "Mock", str(index), "Mock", "Mock"] if provider == "abc" else [
            f"2024-01-{index % 28 + 1:02}", "CNY", "-10.25", "1000.00", "Mock", "Mock", "Mock"]
        page = [(40, 750, bank + "交易明细"), (40, 725, "账号：990000000000001234"), (40, 700, "币种：人民币")]
        page += [(40 + column * 110, 650, label) for column, label in enumerate(labels)]
        page += [(40 + column * 110, 600, value) for column, value in enumerate(values)]
        texts.append(page)
    characters = sorted({char for page in texts for _, _, value in page for char in value})
    cmap = ("/CIDInit /ProcSet findresource begin 12 dict begin begincmap "
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def "
            "/CMapName /Mock def /CMapType 2 def 1 begincodespacerange <0000> <FFFF> endcodespacerange "
            + f"{len(characters)} beginbfchar "
            + " ".join(f"<{ord(c):04X}> <{ord(c):04X}>" for c in characters)
            + " endbfchar endcmap CMapName currentdict /CMap defineresource pop end end").encode()
    def stream(payload):
        return f"<< /Length {len(payload)} >>\nstream\n".encode() + payload + b"\nendstream"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"",
               b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light /Encoding /Identity-H /DescendantFonts [4 0 R] /ToUnicode 5 0 R >>",
               b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /DW 1000 >>",
               stream(cmap)]
    kids = []
    for page in texts:
        page_id, content_id = len(objects) + 1, len(objects) + 2
        kids.append(f"{page_id} 0 R")
        content = "\n".join(f"BT /F1 9 Tf {x} {y} Td <{value.encode('utf-16-be').hex()}> Tj ET" for x, y, value in page).encode()
        objects += [f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 1100 800] /Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>".encode(), stream(content)]
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {pages} >>".encode()
    result, offsets = b"%PDF-1.4\n", [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(result)
    result += f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
    result += b"".join(f"{offset:010} 00000 n \n".encode() for offset in offsets[1:])
    return result + f"trailer << /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
