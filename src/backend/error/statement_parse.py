"""Finite public parse diagnostics; never expose an exception or bill value."""
PARSE_ISSUES = {
    "PARSE_ERROR": ("账单未能解析，具体原因尚未识别。", "请检查文件类型和来源，重新导出；反馈时提供脱敏错误码，不提供密码或原账单。"),
    "HEADER_NOT_FOUND": ("未找到可识别的交易表头。", "请保留完整明细表头，选择正确来源后重新导出。"),
    "HEADER_INVALID": ("明细表头重复或不完整。", "请使用银行或平台原始明细导出，避免合并或改名金额列。"),
    "SOURCE_REQUIRED": ("无法可靠识别账单来源。", "请在上传时明确选择对应银行或平台。"),
    "SOURCE_MISMATCH": ("文件内容与所选来源不一致。", "请核对来源选择，按实际银行或平台重新上传。"),
    "SOURCE_AMBIGUOUS": ("文件包含多个来源，无法可靠分配。", "请按银行或平台拆成独立文件。"),
    "OWN_ACCOUNT_REQUIRED": ("银行明细缺少本方账户信息。", "请保留导出文件中的本方账户信息，不用对方账号或尾号替代。"),
    "OWN_ACCOUNT_AMBIGUOUS": ("同一账单段出现多个本方账号。", "请按具体来源账户拆分导出。"),
    "FORMAT_UNSUPPORTED": ("文件格式不受支持。", "请使用 CSV、XLS、XLSX、文本型 PDF，或包含单个明细文件的 ZIP。"),
    "WORKBOOK_INVALID": ("工作簿损坏或没有明细工作表。", "请重新导出 XLS/XLSX；不要只修改文件扩展名。"),
    "PDF_TEXT_REQUIRED": ("PDF 没有可读取文本。", "请导出电子交易明细，不使用扫描图片。"),
    "PDF_PARSE_ERROR": ("PDF 损坏、加密或文本格式无法解析。", "请检查密码并重新导出文本明细；当前不能确定是哪一种原因。"),
    "PDF_FORMAT_UNKNOWN": ("尚未识别该 PDF 的银行明细格式。", "请改用对应银行的 CSV/XLS/XLSX 明细导出。"),
    "ZIP_INVALID": ("ZIP 损坏或包含不适用的明细文件。", "请只保留一个 CSV、XLS 或 XLSX 文件后重新压缩。"),
    "ZIP_PASSWORD_INVALID": ("ZIP 需要密码或密码不正确。", "请核对密码，或直接上传解压后的明细；不要在故障反馈中提供密码。"),
    "STATEMENT_INCOMPLETE": ("解析条数或金额与文件声明不一致。", "请检查缺失页面、明细和汇总；不能把不完整明细当成成功导入。"),
    "PARSE_LIMIT": ("解析超过安全容量或时间限制。", "请按时间区间拆分导出，减少文件大小、行数或页数。"),
}

_MESSAGES = {
    "未找到交易表头": "HEADER_NOT_FOUND",
    "表头存在重复字段，不能可靠匹配": "HEADER_INVALID",
    "PDF 表头不完整，不能可靠定位金额列": "HEADER_INVALID",
    "来源证据不足，请在预览中选择来源": "SOURCE_REQUIRED",
    "PDF source does not match selected bank": "SOURCE_MISMATCH",
    "文件包含多个来源标题，请检查文件内容": "SOURCE_AMBIGUOUS",
    "Workbook contains different source providers": "SOURCE_AMBIGUOUS",
    "PDF 包含不同银行的账单页": "SOURCE_AMBIGUOUS",
    "银行明细缺少本方账号，请在文件中保留账户信息": "OWN_ACCOUNT_REQUIRED",
    "同一账单段含多个本方账号，不能可靠分配来源身份": "OWN_ACCOUNT_AMBIGUOUS",
    "支持 CSV、XLS、XLSX、PDF 和 ZIP": "FORMAT_UNSUPPORTED",
    "不支持的账单来源": "SOURCE_REQUIRED",
    "文件没有明细工作表": "WORKBOOK_INVALID",
    "Invalid statement workbook": "WORKBOOK_INVALID",
    "PDF 没有可读取文本；请提供银行电子明细，而非扫描图片": "PDF_TEXT_REQUIRED",
    "PDF 损坏、加密或文本无法解析": "PDF_PARSE_ERROR",
    "尚未识别该 PDF 的银行明细格式": "PDF_FORMAT_UNKNOWN",
    "Invalid ZIP archive": "ZIP_INVALID",
    "ZIP contains an unsupported file entry": "ZIP_INVALID",
    "ZIP must contain exactly one CSV, XLS, or XLSX file": "ZIP_INVALID",
    "ZIP password is required or invalid": "ZIP_PASSWORD_INVALID",
    "解析条数与文件声明不一致，未提交不完整明细": "STATEMENT_INCOMPLETE",
    "Excel 解压内容超过安全上限": "PARSE_LIMIT",
    "PDF 超过 100 页，请按区间导出": "PARSE_LIMIT",
    "Statement exceeds 20000 source rows": "PARSE_LIMIT",
    "文件超过20,000来源行": "PARSE_LIMIT",
    "ZIP decoded content exceeds the safety limit": "PARSE_LIMIT",
    "ZIP entry exceeds the safety limit": "PARSE_LIMIT",
}


class StatementParseError(ValueError):
    def __init__(self, code):
        self.code = code if code in PARSE_ISSUES else "PARSE_ERROR"
        super().__init__(PARSE_ISSUES[self.code][0])


def public_parse_code(error):
    if isinstance(error, StatementParseError):
        return error.code
    # Recognize only our parser's fixed messages. Unrecognized external
    # exceptions, even ones containing private source text, stay generic.
    message = str(error)
    if message.startswith("正文明确来自") and message.endswith("与所选来源不符"):
        return "SOURCE_MISMATCH"
    if message.endswith("与文件汇总不一致，请核对解析结果"):
        return "STATEMENT_INCOMPLETE"
    return _MESSAGES.get(message, "PARSE_ERROR")
