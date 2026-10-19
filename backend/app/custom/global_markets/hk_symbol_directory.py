"""Bounded, in-memory search over HKEX's public securities workbooks."""
from __future__ import annotations

import io
import posixpath
import re
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass
from datetime import date
from xml.etree import ElementTree as ET

import httpx

from .catalog import MarketDataError, _search_key, normalize_symbol

ENGLISH_LIST_URL = "https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx"
TRADITIONAL_LIST_URL = "https://www.hkex.com.hk/chi/services/trading/securities/securitieslists/ListOfSecurities_c.xlsx"

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_BYTES = 24 * 1024 * 1024
MAX_ENTRY_BYTES = 20 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 64
MAX_SOURCE_ROWS = 25_000
MIN_SOURCE_ROWS = 10_000
MIN_CATALOG_ROWS = 1_000
MAX_COLUMNS = 18
MAX_SHARED_STRINGS = 100_000
MAX_CELL_CHARS = 512
MAX_RESULTS = 20
MAX_QUERY_LENGTH = 60
CACHE_SECONDS = 24 * 60 * 60
REQUEST_TIMEOUT_SECONDS = 8

_MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_DOC_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PACKAGE_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_HKEX_SOURCE = "HKEX Full List of Securities"
_MIME_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/zip",
}

_HEADERS = {
    "en": {
        "title": "List of Securities",
        "code": "Stock Code",
        "name": "Name of Securities",
        "category": "Category",
        "subcategory": "Sub-Category",
        "currency": "Trading Currency",
    },
    "zh": {
        "title": "證券名單",
        "code": "股份代號",
        "name": "股份名稱",
        "category": "分類",
        "subcategory": "次分類",
        "currency": "交易貨幣",
    },
}

_ALLOWED_ENGLISH = {
    ("Equity", "Equity Securities (Main Board)"): ("stock", "股本", "股本證券(主板)"),
    ("Equity", "Equity Securities (GEM)"): ("stock", "股本", "股本證券(創業板)"),
    ("Exchange Traded Products", "Exchange Traded Funds"): ("etf", "交易所買賣產品", "交易所買賣基金"),
    ("Exchange Traded Products", "Leveraged and Inverse"): ("etf", "交易所買賣產品", "槓桿及反向產品"),
}


@dataclass(frozen=True)
class _Security:
    code: str
    name: str
    category: str
    subcategory: str
    currency: str


@dataclass(frozen=True)
class _Workbook:
    title: str
    date_title: str
    source_date: str | None
    rows: dict[str, _Security]


def _parse_source_date(value: str) -> str | None:
    match = re.search(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)", value)
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _read_member(archive: zipfile.ZipFile, name: str) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > MAX_ENTRY_BYTES:
        raise ValueError("HKEX目录工作簿单项超出大小限制")
    with archive.open(info) as stream:
        body = stream.read(MAX_ENTRY_BYTES + 1)
    if len(body) > MAX_ENTRY_BYTES or len(body) != info.file_size:
        raise ValueError("HKEX目录工作簿单项大小无效")
    return body


def _parse_xml(payload: bytes) -> ET.Element:
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", payload, re.IGNORECASE):
        raise ValueError("HKEX目录工作簿包含不支持的XML声明")
    return ET.fromstring(payload)


def _safe_target(target: str) -> str:
    if not target or target.startswith(("/", "\\")) or "\\" in target:
        raise ValueError("HKEX目录工作簿路径无效")
    path = posixpath.normpath(posixpath.join("xl", target))
    if path == "xl" or path.startswith("../") or not path.startswith("xl/"):
        raise ValueError("HKEX目录工作簿路径无效")
    return path


def _shared_strings(archive: zipfile.ZipFile, path: str | None) -> list[str]:
    if path is None:
        return []
    root = _parse_xml(_read_member(archive, path))
    items = root.findall(f"{_MAIN_NS}si")
    if len(items) > MAX_SHARED_STRINGS:
        raise ValueError("HKEX目录共享字符串超过数量限制")
    result = []
    for item in items:
        value = "".join(node.text or "" for node in item.iter(f"{_MAIN_NS}t"))
        if len(value) > MAX_CELL_CHARS:
            raise ValueError("HKEX目录单元格文本过长")
        result.append(value)
    return result


def _cell_text(cell: ET.Element, shared: list[str]) -> str:
    if cell.find(f"{_MAIN_NS}f") is not None:
        raise ValueError("HKEX目录工作簿含公式单元格")
    cell_type = cell.get("t", "")
    if cell_type == "inlineStr":
        inline = cell.find(f"{_MAIN_NS}is")
        value = "" if inline is None else "".join(
            node.text or "" for node in inline.iter(f"{_MAIN_NS}t")
        )
    else:
        value_node = cell.find(f"{_MAIN_NS}v")
        value = "" if value_node is None or value_node.text is None else value_node.text
        if cell_type == "s" and value:
            try:
                shared_index = int(value)
                if shared_index < 0 or shared_index >= len(shared):
                    raise ValueError("HKEX目录共享字符串索引无效")
                value = shared[shared_index]
            except ValueError as error:
                raise ValueError("HKEX目录共享字符串索引无效") from error
        elif cell_type == "e":
            raise ValueError("HKEX目录工作簿含错误单元格")
    if len(value) > MAX_CELL_CHARS:
        raise ValueError("HKEX目录单元格文本过长")
    return value.strip()


def _column_index(reference: str) -> tuple[int, int]:
    match = re.fullmatch(r"([A-Z]{1,2})([1-9][0-9]{0,5})", reference)
    if not match:
        raise ValueError("HKEX目录单元格位置无效")
    letters, row_text = match.groups()
    column = 0
    for letter in letters:
        column = column * 26 + ord(letter) - ord("A") + 1
    if column > MAX_COLUMNS:
        raise ValueError("HKEX目录列数超过限制")
    return column - 1, int(row_text)


def _worksheet_path(archive: zipfile.ZipFile) -> tuple[str, str | None]:
    workbook = _parse_xml(_read_member(archive, "xl/workbook.xml"))
    workbook_rels = _parse_xml(_read_member(archive, "xl/_rels/workbook.xml.rels"))
    sheet = next(
        (node for node in workbook.findall(f".//{_MAIN_NS}sheet")
         if node.get("name") == "ListOfSecurities"),
        None,
    )
    if sheet is None:
        raise ValueError("HKEX目录缺少证券名单工作表")
    relationship_id = sheet.get(f"{_DOC_REL_NS}id")
    targets: dict[str, tuple[str, str]] = {}
    for rel in workbook_rels.findall(f"{_PACKAGE_REL_NS}Relationship"):
        rel_id = rel.get("Id")
        rel_type = rel.get("Type", "")
        target_mode = rel.get("TargetMode", "").casefold()
        target = rel.get("Target", "")
        if target_mode == "external" and (rel_type.endswith("/worksheet") or rel_type.endswith("/sharedStrings")):
            raise ValueError("HKEX目录工作表不能引用外部关系")
        if target_mode == "external":
            continue
        if rel_id:
            if rel_id in targets:
                raise ValueError("HKEX目录工作簿关系重复")
            targets[rel_id] = (rel_type, target)
    sheet_rel = targets.get(relationship_id or "")
    if not sheet_rel or not sheet_rel[0].endswith("/worksheet"):
        raise ValueError("HKEX目录工作表关系无效")
    sheet_path = _safe_target(sheet_rel[1])
    if not sheet_path.startswith("xl/worksheets/"):
        raise ValueError("HKEX目录工作表路径无效")
    shared_rel = next(
        ((rel_type, target) for rel_type, target in targets.values()
         if rel_type.endswith("/sharedStrings")),
        None,
    )
    shared_path = None
    if shared_rel:
        if not shared_rel[0].endswith("/sharedStrings"):
            raise ValueError("HKEX目录共享字符串关系无效")
        shared_path = _safe_target(shared_rel[1])
        if shared_path != "xl/sharedStrings.xml":
            raise ValueError("HKEX目录共享字符串路径无效")
    return sheet_path, shared_path


def _parse_workbook(payload: bytes, language: str) -> _Workbook:
    if language not in _HEADERS:
        raise ValueError("HKEX目录语言无效")
    if not payload or len(payload) > MAX_FILE_BYTES:
        raise ValueError("HKEX目录工作簿响应超过大小限制")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(infos) > MAX_ARCHIVE_ENTRIES or len(set(names)) != len(names):
            raise ValueError("HKEX目录工作簿条目数无效")
        if any(
            name.startswith(("/", "\\")) or "\\" in name
            or ".." in name.split("/")
            for name in names
        ):
            raise ValueError("HKEX目录工作簿包含越界路径")
        if any(
            "vbaproject" in name.casefold()
            or name.casefold().startswith("xl/externallinks/")
            for name in names
        ):
            raise ValueError("HKEX目录工作簿包含不支持的外部或宏部件")
        if sum(info.file_size for info in infos) > MAX_ARCHIVE_BYTES:
            raise ValueError("HKEX目录工作簿解压大小超过限制")
        content_types = _read_member(archive, "[Content_Types].xml")
        if b"macroenabled" in content_types.lower() or b"macrosheet" in content_types.lower():
            raise ValueError("HKEX目录工作簿不支持宏内容")
        sheet_path, shared_path = _worksheet_path(archive)
        shared = _shared_strings(archive, shared_path)
        sheet = _parse_xml(_read_member(archive, sheet_path))

    sheet_data = sheet.find(f"{_MAIN_NS}sheetData")
    if sheet_data is None:
        raise ValueError("HKEX目录工作表缺少数据")
    rows: dict[int, dict[int, str]] = {}
    for row in sheet_data.findall(f"{_MAIN_NS}row"):
        row_text = row.get("r", "")
        if not row_text.isdigit():
            raise ValueError("HKEX目录行号无效")
        row_number = int(row_text)
        if row_number < 1 or row_number > MAX_SOURCE_ROWS + 3 or row_number in rows:
            raise ValueError("HKEX目录行数或行号无效")
        values: dict[int, str] = {}
        for cell in row.findall(f"{_MAIN_NS}c"):
            column, cell_row = _column_index(cell.get("r", ""))
            if cell_row != row_number or column in values:
                raise ValueError("HKEX目录单元格引用无效")
            values[column] = _cell_text(cell, shared)
        rows[row_number] = values

    header = _HEADERS[language]
    title = rows.get(1, {}).get(0, "")
    if title != header["title"]:
        raise ValueError("HKEX目录工作簿标题无效")
    date_title = rows.get(2, {}).get(0, "")
    source_date = _parse_source_date(date_title)
    header_values = rows.get(3, {})
    header_columns = {value: index for index, value in header_values.items() if value}
    fields = ("code", "name", "category", "subcategory", "currency")
    if any(
        sum(value == header[field] for value in header_values.values()) != 1
        for field in fields
    ):
        raise ValueError("HKEX目录工作簿字段无效")

    column_by_field = {field: header_columns[header[field]] for field in fields}
    result: dict[str, _Security] = {}
    for row_number in sorted(number for number in rows if number > 3):
        values = rows[row_number]
        if not any(value for value in values.values()):
            continue
        code = values.get(column_by_field["code"], "").strip()
        name = values.get(column_by_field["name"], "").strip()
        category = values.get(column_by_field["category"], "").strip()
        subcategory = values.get(column_by_field["subcategory"], "").strip()
        currency = values.get(column_by_field["currency"], "").strip().upper()
        if not re.fullmatch(r"[0-9]{5}", code) or not name:
            raise ValueError("HKEX目录证券代码或名称无效")
        if code in result:
            raise ValueError("HKEX目录包含重复证券代码")
        if len(result) >= MAX_SOURCE_ROWS:
            raise ValueError("HKEX目录证券记录超过数量限制")
        result[code] = _Security(code, name, category, subcategory, currency)
    if len(result) < 1:
        raise ValueError("HKEX目录证券记录为空")
    return _Workbook(title, date_title, source_date, result)


def _merge_workbooks(english: _Workbook, traditional: _Workbook) -> tuple[dict, ...]:
    if len(english.rows) < MIN_SOURCE_ROWS or len(traditional.rows) < MIN_SOURCE_ROWS:
        raise ValueError("HKEX目录证券记录数量不足")
    if english.rows.keys() != traditional.rows.keys():
        raise ValueError("HKEX中英文证券目录代码不一致")
    if (english.source_date and traditional.source_date
            and english.source_date != traditional.source_date):
        raise ValueError("HKEX中英文证券目录日期不一致")
    source_date = (
        english.source_date
        if english.source_date and english.source_date == traditional.source_date
        else None
    )
    rows = []
    for code in sorted(english.rows):
        en = english.rows[code]
        zh = traditional.rows[code]
        if en.currency != zh.currency:
            raise ValueError("HKEX中英文证券目录货币不一致")
        allowed = _ALLOWED_ENGLISH.get((en.category, en.subcategory))
        if allowed is None or en.currency != "HKD":
            continue
        kind, expected_category_zh, expected_subcategory_zh = allowed
        if (zh.category, zh.subcategory) != (expected_category_zh, expected_subcategory_zh):
            raise ValueError("HKEX中英文证券目录类别不一致")
        rows.append({
            "symbol": normalize_symbol("hk", code),
            "name": zh.name,
            "name_en": en.name,
            "market": "hk",
            "kind": kind,
            "category": en.category,
            "subcategory": en.subcategory,
            "category_zh": zh.category,
            "subcategory_zh": zh.subcategory,
            "currency": "HKD",
            "source": _HKEX_SOURCE,
            "source_date": source_date,
        })
    if len(rows) < MIN_CATALOG_ROWS:
        raise ValueError("HKEX目录符合港币普通股与交易所产品条件的记录不足")
    return tuple(rows)


def _read_response(client: httpx.Client, url: str) -> bytes:
    with client.stream(
        "GET",
        url,
        headers={"Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    ) as response:
        if response.status_code != 200:
            raise ValueError("HKEX证券目录请求失败")
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
        if content_type not in _MIME_TYPES:
            raise ValueError("HKEX证券目录响应类型无效")
        length = response.headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) > MAX_FILE_BYTES):
            raise ValueError("HKEX证券目录响应超过大小限制")
        content = bytearray()
        for chunk in response.iter_bytes(chunk_size=64 * 1024):
            content.extend(chunk)
            if len(content) > MAX_FILE_BYTES:
                raise ValueError("HKEX证券目录响应超过大小限制")
    return bytes(content)


def _download_rows() -> tuple[dict, ...]:
    # Both language files must validate and join before replacing the directory.
    with httpx.Client(
        timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=REQUEST_TIMEOUT_SECONDS),
        follow_redirects=False,
    ) as client:
        english_payload = _read_response(client, ENGLISH_LIST_URL)
        traditional_payload = _read_response(client, TRADITIONAL_LIST_URL)
    english = _parse_workbook(english_payload, "en")
    traditional = _parse_workbook(traditional_payload, "zh")
    return _merge_workbooks(english, traditional)


def load_rows() -> list[dict]:
    """Download and validate both language lists; service owns the success cache."""
    try:
        rows = _download_rows()
    except (
        httpx.HTTPError,
        OSError,
        UnicodeError,
        ValueError,
        KeyError,
        RuntimeError,
        zipfile.BadZipFile,
        ET.ParseError,
        zlib.error,
        NotImplementedError,
    ) as error:
        raise MarketDataError("HKEX证券目录暂不可用") from error
    return [dict(row) for row in rows]


def _key(value: str) -> str:
    return _search_key(unicodedata.normalize("NFKC", value))


def search(query: str, rows: tuple[dict, ...] | list[dict] | None = None) -> list[dict]:
    """Search eligible HKD ordinary shares and ETFs without returning quotes."""
    query = query.strip()
    if not query or len(query) > MAX_QUERY_LENGTH:
        return []
    key = _key(query)
    if not key:
        return []
    numeric_query = query.upper().removesuffix(".HK")
    code_query = numeric_query if re.fullmatch(r"[0-9]{1,5}", numeric_query) else ""
    rows = load_rows() if rows is None else rows
    matches: list[tuple[int, str, dict]] = []
    for row in rows:
        code = row["symbol"].removesuffix(".HK")
        name_keys = (_key(row["name"]), _key(row["name_en"]))
        if code_query and code == code_query.zfill(5):
            score = 0
        elif code_query and code.startswith(code_query):
            score = 1
        elif key in {_key(code), _key(row["symbol"])}:
            score = 0
        elif any(key == name_key for name_key in name_keys):
            score = 2
        elif any(name_key.startswith(key) for name_key in name_keys):
            score = 3
        elif len(key) >= 2 and any(key in name_key for name_key in name_keys):
            score = 4
        else:
            continue
        matches.append((score, code, row))
    matches.sort(key=lambda item: (item[0], item[1]))
    return [dict(row) for _, _, row in matches[:MAX_RESULTS]]
