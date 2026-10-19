from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

import pytest
from app.custom.global_markets import hk_symbol_directory as directory
from app.custom.global_markets.catalog import MarketDataError

_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _column(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _cell(reference: str, value: str, *, formula: bool = False) -> str:
    formula_xml = "<x:f>1+1</x:f>" if formula else ""
    return f'<x:c r="{reference}" t="str">{formula_xml}<x:v>{escape(value)}</x:v></x:c>'


def _workbook(language: str, securities: list[dict], date_title: str, *, formula: bool = False,
              extra_entries: dict[str, bytes] | None = None) -> bytes:
    if language == "en":
        title = "List of Securities"
        headers = ["Stock Code", "Name of Securities", "Category", "Sub-Category"]
        headers.extend("" for _ in range(12))
        headers.append("Trading Currency")
        fields = ("code", "name_en", "category", "subcategory", "currency")
    else:
        title = "證券名單"
        headers = ["股份代號", "股份名稱", "分類", "次分類"]
        headers.extend("" for _ in range(12))
        headers.append("交易貨幣")
        fields = ("code", "name_zh", "category_zh", "subcategory_zh", "currency")
    row_xml = [f'<x:row r="1">{_cell("A1", title)}</x:row>']
    row_xml.append(f'<x:row r="2">{_cell("A2", date_title)}</x:row>')
    row_xml.append(
        '<x:row r="3">'
        + "".join(_cell(f"{_column(index)}3", value) for index, value in enumerate(headers, start=1))
        + "</x:row>"
    )
    for row_number, security in enumerate(securities, start=4):
        values = (security[field] for field in fields)
        cells = []
        for column, value in zip((1, 2, 3, 4, 17), values, strict=True):
            ref = f"{_column(column)}{row_number}"
            cells.append(_cell(ref, value, formula=formula and column == 2))
        row_xml.append(f'<x:row r="{row_number}">{"".join(cells)}</x:row>')

    worksheet = (
        f'<x:worksheet xmlns:x="{_MAIN_NS}" xmlns:r="{_REL_NS}"><x:sheetData>'
        + "".join(row_xml)
        + "</x:sheetData></x:worksheet>"
    )
    workbook_xml = (
        f'<x:workbook xmlns:x="{_MAIN_NS}" xmlns:r="{_REL_NS}">'
        '<x:sheets><x:sheet name="ListOfSecurities" sheetId="1" r:id="rId1"/>'
        "</x:sheets></x:workbook>"
    )
    workbook_rels = (
        f'<Relationships xmlns="{_PACKAGE_REL_NS}">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    content_types = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        "</Types>"
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
        for name, body in (extra_entries or {}).items():
            archive.writestr(name, body)
    return output.getvalue()


def _security(code: str, name_en: str, name_zh: str, category: str, subcategory: str,
              category_zh: str, subcategory_zh: str, currency: str = "HKD") -> dict:
    return {
        "code": code,
        "name_en": name_en,
        "name_zh": name_zh,
        "category": category,
        "subcategory": subcategory,
        "category_zh": category_zh,
        "subcategory_zh": subcategory_zh,
        "currency": currency,
    }


def _pair(monkeypatch, securities: list[dict], *, date_en: str = "Updated as at 20/10/2026",
          date_zh: str = "截至 20/10/2026"):
    monkeypatch.setattr(directory, "MIN_SOURCE_ROWS", 1)
    monkeypatch.setattr(directory, "MIN_CATALOG_ROWS", 1)
    english = directory._parse_workbook(_workbook("en", securities, date_en), "en")
    traditional = directory._parse_workbook(_workbook("zh", securities, date_zh), "zh")
    return directory._merge_workbooks(english, traditional)


def test_parser_preserves_leading_zero_names_categories_currency_and_source_date(monkeypatch):
    records = [
        _security(
            "00700", "TENCENT", "騰訊控股", "Equity", "Equity Securities (Main Board)",
            "股本", "股本證券(主板)",
        ),
    ]
    rows = _pair(monkeypatch, records)

    assert rows[0]["symbol"] == "00700.HK"
    assert rows[0]["name"] == "騰訊控股"
    assert rows[0]["name_en"] == "TENCENT"
    assert rows[0]["category"] == "Equity"
    assert rows[0]["subcategory"] == "Equity Securities (Main Board)"
    assert rows[0]["currency"] == "HKD"
    assert rows[0]["source"] == "HKEX Full List of Securities"
    assert rows[0]["source_date"] == "2026-10-20"


def test_merge_includes_only_hkd_ordinary_shares_etfs_and_leveraged_inverse_etps(monkeypatch):
    records = [
        _security("00001", "MAIN BOARD", "主板股票", "Equity", "Equity Securities (Main Board)", "股本", "股本證券(主板)"),
        _security("00002", "GEM", "創業板股票", "Equity", "Equity Securities (GEM)", "股本", "股本證券(創業板)"),
        _security("00003", "INVESTMENT COMPANY", "投資公司", "Equity", "Investment Companies", "股本", "投資公司"),
        _security("00004", "ETF", "交易所基金", "Exchange Traded Products", "Exchange Traded Funds", "交易所買賣產品", "交易所買賣基金"),
        _security("00005", "LEVERAGED ETP", "槓桿產品", "Exchange Traded Products", "Leveraged and Inverse", "交易所買賣產品", "槓桿及反向產品"),
        _security("00006", "DEBT", "債券", "Debt Securities", "", "債券", ""),
        _security("00007", "WARRANT", "權證", "Derivative Warrants", "", "衍生權證", ""),
        _security("00008", "USD ETF", "美元基金", "Exchange Traded Products", "Exchange Traded Funds", "交易所買賣產品", "交易所買賣基金", "USD"),
        _security("00009", "RMB STOCK", "人民幣股票", "Equity", "Equity Securities (Main Board)", "股本", "股本證券(主板)", "RMB"),
    ]

    rows = _pair(monkeypatch, records)

    assert [row["symbol"] for row in rows] == ["00001.HK", "00002.HK", "00004.HK", "00005.HK"]
    assert [row["kind"] for row in rows] == ["stock", "stock", "etf", "etf"]
    assert rows[-1]["subcategory"] == "Leveraged and Inverse"
    assert rows[-1]["subcategory_zh"] == "槓桿及反向產品"
    assert all(row["currency"] == "HKD" for row in rows)


def test_directory_search_matches_partial_existing_traditional_simplified_map_and_code(monkeypatch):
    records = [
        _security("00981", "SMIC", "中芯國際", "Equity", "Equity Securities (Main Board)", "股本", "股本證券(主板)"),
    ]
    rows = _pair(monkeypatch, records)

    assert directory.search("中芯国际", rows=rows)[0]["symbol"] == "00981.HK"
    assert directory.search("SMIC", rows=rows)[0]["name"] == "中芯國際"
    assert directory.search("00981", rows=rows)[0]["symbol"] == "00981.HK"
    assert directory.search("!!!") == []
    assert directory.search("") == []


def test_missing_source_title_date_stays_missing(monkeypatch):
    records = [
        _security("00981", "SMIC", "中芯國際", "Equity", "Equity Securities (Main Board)", "股本", "股本證券(主板)"),
    ]
    rows = _pair(monkeypatch, records, date_en="Updated as at", date_zh="截至")
    assert rows[0]["source_date"] is None
    assert directory._parse_source_date("Updated as at 31/02/2026") is None


def test_mismatched_language_code_sets_are_rejected(monkeypatch):
    monkeypatch.setattr(directory, "MIN_SOURCE_ROWS", 1)
    monkeypatch.setattr(directory, "MIN_CATALOG_ROWS", 1)
    english_records = [
        _security("00001", "ONE", "一", "Equity", "Equity Securities (Main Board)", "股本", "股本證券(主板)"),
    ]
    traditional_records = [
        _security("00002", "TWO", "二", "Equity", "Equity Securities (Main Board)", "股本", "股本證券(主板)"),
    ]
    english = directory._parse_workbook(_workbook("en", english_records, "date absent"), "en")
    traditional = directory._parse_workbook(_workbook("zh", traditional_records, "date absent"), "zh")

    with pytest.raises(ValueError, match="代码不一致"):
        directory._merge_workbooks(english, traditional)


def test_parser_rejects_formulas_macro_parts_and_archive_limit_violations(monkeypatch):
    record = _security("00001", "ONE", "一", "Equity", "Equity Securities (Main Board)", "股本", "股本證券(主板)")
    formula_book = _workbook("en", [record], "date absent", formula=True)
    with pytest.raises(ValueError, match="公式"):
        directory._parse_workbook(formula_book, "en")

    macro_book = _workbook("en", [record], "date absent", extra_entries={"xl/vbaProject.bin": b"macro"})
    with pytest.raises(ValueError, match="外部或宏"):
        directory._parse_workbook(macro_book, "en")

    ordinary_book = _workbook("en", [record], "date absent")
    monkeypatch.setattr(directory, "MAX_ARCHIVE_ENTRIES", 2)
    with pytest.raises(ValueError, match="条目数"):
        directory._parse_workbook(ordinary_book, "en")


def test_http_reader_requires_official_workbook_response_and_enforces_size():
    body = b"xlsx"

    class Response:
        status_code = 200

        def __init__(self, headers):
            self.headers = headers

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_bytes(self, chunk_size):
            assert chunk_size == 64 * 1024
            yield body

    class Client:
        def __init__(self, headers=None):
            self.headers = headers or {
                "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "content-length": "4",
            }

        def stream(self, method, url, *, headers):
            assert method == "GET"
            assert headers == {"Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
            return Response(self.headers)

    assert directory._read_response(Client(), directory.ENGLISH_LIST_URL) == body
    with pytest.raises(ValueError, match="响应类型"):
        directory._read_response(
            Client({"content-type": "text/html", "content-length": "4"}),
            directory.ENGLISH_LIST_URL,
        )


def test_http_reader_rejects_advertised_and_streamed_oversized_responses():
    class Response:
        status_code = 200

        def __init__(self, headers, body):
            self.headers = headers
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_bytes(self, chunk_size):
            yield self.body

    class Client:
        def __init__(self, response):
            self.response = response

        def stream(self, *_args, **_kwargs):
            return self.response

    headers = {
        "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "content-length": str(directory.MAX_FILE_BYTES + 1),
    }
    with pytest.raises(ValueError, match="大小限制"):
        directory._read_response(Client(Response(headers, b"")), directory.ENGLISH_LIST_URL)

    headers.pop("content-length")
    oversized = b"x" * (directory.MAX_FILE_BYTES + 1)
    with pytest.raises(ValueError, match="大小限制"):
        directory._read_response(Client(Response(headers, oversized)), directory.ENGLISH_LIST_URL)


def test_refresh_uses_two_bounded_anonymous_https_requests(monkeypatch):
    records = [
        _security("00981", "SMIC", "中芯國際", "Equity", "Equity Securities (Main Board)", "股本", "股本證券(主板)"),
    ]
    payloads = {
        directory.ENGLISH_LIST_URL: _workbook("en", records, "Updated as at 20/10/2026"),
        directory.TRADITIONAL_LIST_URL: _workbook("zh", records, "截至 20/10/2026"),
    }
    calls = []
    monkeypatch.setattr(directory, "MIN_SOURCE_ROWS", 1)
    monkeypatch.setattr(directory, "MIN_CATALOG_ROWS", 1)

    class Response:
        status_code = 200

        def __init__(self, body):
            self.body = body
            self.headers = {
                "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "content-length": str(len(body)),
            }

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_bytes(self, chunk_size):
            assert chunk_size == 64 * 1024
            yield self.body

    class Client:
        def __init__(self, *, timeout, follow_redirects):
            assert timeout.connect == directory.REQUEST_TIMEOUT_SECONDS
            assert timeout.read == directory.REQUEST_TIMEOUT_SECONDS
            assert follow_redirects is False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def stream(self, method, url, *, headers):
            calls.append((method, url, headers))
            return Response(payloads[url])

    monkeypatch.setattr(directory.httpx, "Client", Client)

    rows = directory._download_rows()

    assert [call[1] for call in calls] == [directory.ENGLISH_LIST_URL, directory.TRADITIONAL_LIST_URL]
    assert all(method == "GET" for method, _, _ in calls)
    assert all(headers == {"Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
               for _, _, headers in calls)
    assert rows[0]["symbol"] == "00981.HK"


def test_loader_wraps_bad_download_without_exposing_response_details(monkeypatch):
    monkeypatch.setattr(directory, "_download_rows", lambda: (_ for _ in ()).throw(ValueError("private body")))

    with pytest.raises(MarketDataError, match="HKEX证券目录暂不可用") as error:
        directory.load_rows()

    assert "private body" not in str(error.value)
