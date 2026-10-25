# ruff: noqa: RUF001 -- Native Chinese research text.
from __future__ import annotations

import io
import sys
import types
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.custom.research_tools import library


def _docx(document_xml: str, *, extra: dict[str, bytes] | None = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document_xml)
        for name, contents in (extra or {}).items():
            archive.writestr(name, contents)
    return output.getvalue()


def _pdf_with_text_on_second_page() -> bytes:
    pypdf = pytest.importorskip("pypdf")
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=612, height=792)
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 72 720 Td (needle) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _fake_pdf_reader(monkeypatch, page_texts: list[str | None]) -> None:
    module = types.ModuleType("pypdf")

    class Page:
        def __init__(self, text: str | None) -> None:
            self.text = text

        def extract_text(self) -> str | None:
            return self.text

    class PdfReader:
        def __init__(self, _source, *, strict: bool) -> None:
            assert strict is False
            self.is_encrypted = False
            self.pages = [Page(text) for text in page_texts]

    module.PdfReader = PdfReader
    monkeypatch.setitem(sys.modules, "pypdf", module)


def test_add_list_and_deduplicate_by_content_id(tmp_path: Path) -> None:
    first = library.add_report(tmp_path, "notes.txt", "本地研究资料".encode())
    second = library.add_report(tmp_path, "renamed.txt", "本地研究资料".encode())

    assert second["duplicate"] is True
    assert second["item"]["id"] == first["item"]["id"]
    listed = library.list_reports(tmp_path)["items"]
    assert len(listed) == 1
    assert listed[0]["name"] == "notes.txt"
    assert not {"content", "text", "segments", "body"}.intersection(listed[0])


def test_searches_chinese_and_returns_line_citation_and_snippet(tmp_path: Path) -> None:
    library.add_report(
        tmp_path,
        "观察.md",
        "概览\n成交量趋势需要连续观察\n附注".encode(),
    )

    result = library.search_reports(tmp_path, "成交量趋势")

    assert len(result["items"]) == 1
    hit = result["items"][0]
    assert hit["name"] == "观察.md"
    assert hit["page"] is None
    assert "成交量趋势" in hit["snippet"]
    assert hit["citation"] == "观察.md，第 2 行"
    assert hit["score"] > 0


def test_docx_citation_uses_paragraph_not_page(tmp_path: Path) -> None:
    xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>首段</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>定位术语</w:t></w:r></w:p></w:body></w:document>"
    )
    library.add_report(tmp_path, "报告.docx", _docx(xml))

    hit = library.search_reports(tmp_path, "定位术语")["items"][0]

    assert hit["page"] is None
    assert hit["citation"] == "报告.docx，第 2 段"


def test_pdf_search_uses_actual_one_based_page_number(tmp_path: Path) -> None:
    library.add_report(tmp_path, "pages.pdf", _pdf_with_text_on_second_page())

    hit = library.search_reports(tmp_path, "needle")["items"][0]

    assert hit["page"] == 2
    assert hit["citation"] == "pages.pdf，第 2 页"


def test_pdf_page_citation_and_no_text_page_without_optional_install(tmp_path: Path, monkeypatch) -> None:
    _fake_pdf_reader(monkeypatch, [None, "needle"])
    result = library.add_report(tmp_path, "pages.pdf", b"bounded fake PDF")
    hit = library.search_reports(tmp_path, "needle")["items"][0]

    assert result["item"]["page_count"] == 2
    assert hit["page"] == 2
    assert hit["citation"] == "pages.pdf，第 2 页"


def test_blank_pdf_pages_are_not_fabricated_as_text(tmp_path: Path, monkeypatch) -> None:
    _fake_pdf_reader(monkeypatch, [None])
    result = library.add_report(tmp_path, "scan.pdf", b"bounded fake PDF")

    assert result["item"]["page_count"] == 1
    assert result["item"]["searchable"] is False
    assert library.search_reports(tmp_path, "fabricated")["items"] == []


def test_scanned_pdf_has_no_fabricated_search_text(tmp_path: Path) -> None:
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=72, height=72)
    output = io.BytesIO()
    writer.write(output)

    result = library.add_report(tmp_path, "scan.pdf", output.getvalue())

    assert result["item"]["page_count"] == 1
    assert result["item"]["searchable"] is False
    assert library.search_reports(tmp_path, "fabricated")["items"] == []
    listed = library.list_reports(tmp_path)["items"][0]
    assert not {"content", "text", "segments", "body"}.intersection(listed)


def test_missing_pdf_dependency_is_reported_without_parser_details(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "pypdf", None)

    with pytest.raises(ValueError, match="可选依赖 pypdf"):
        library.add_report(tmp_path, "input.pdf", b"%PDF-1.4")


def test_pdf_parser_errors_do_not_echo_document_content(tmp_path: Path, monkeypatch) -> None:
    module = types.ModuleType("pypdf")

    class PdfReader:
        def __init__(self, _source, *, strict: bool) -> None:
            raise ValueError("private document text")

    module.PdfReader = PdfReader
    monkeypatch.setitem(sys.modules, "pypdf", module)

    with pytest.raises(ValueError, match="无法读取 PDF") as exc:
        library.add_report(tmp_path, "input.pdf", b"bounded fake PDF")

    assert "private document text" not in str(exc.value)


def test_rejects_filename_traversal_and_docx_member_traversal(tmp_path: Path) -> None:
    outside = tmp_path.parent / "escape.txt"
    with pytest.raises(ValueError, match="不含路径"):
        library.add_report(tmp_path, "../escape.txt", b"safe")
    assert not outside.exists()

    xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>'
    malicious = _docx(xml, extra={"../escape.txt": b"no extract"})
    with pytest.raises(ValueError, match="无效路径"):
        library.add_report(tmp_path, "unsafe.docx", malicious)
    assert not outside.exists()
    assert library.list_reports(tmp_path)["items"] == []


def test_rejects_high_ratio_docx_before_xml_expansion(tmp_path: Path) -> None:
    xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{'x' * (3 * 1024 * 1024)}</w:t></w:r></w:p></w:body></w:document>"
    )
    compressed = _docx(xml)
    assert len(compressed) < library.MAX_FILE_BYTES

    with pytest.raises(ValueError, match="压缩比例"):
        library.add_report(tmp_path, "bomb.docx", compressed)
    assert library.list_reports(tmp_path)["items"] == []


def test_enforces_per_file_and_total_byte_quotas(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(library, "MAX_FILE_BYTES", 4)
    monkeypatch.setattr(library, "MAX_LIBRARY_BYTES", 4)
    library.add_report(tmp_path, "first.txt", b"four")

    with pytest.raises(ValueError, match="文件大小"):
        library.add_report(tmp_path, "large.txt", b"12345")
    with pytest.raises(ValueError, match="100 MiB"):
        library.add_report(tmp_path, "second.txt", b"x")


def test_enforces_document_count_and_total_text_quotas(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(library, "MAX_LIBRARY_FILES", 1)
    count_dir = tmp_path / "count"
    count_dir.mkdir()
    library.add_report(count_dir, "first.txt", b"one")
    with pytest.raises(ValueError, match="50 份"):
        library.add_report(count_dir, "second.txt", b"two")

    monkeypatch.setattr(library, "MAX_LIBRARY_FILES", 50)
    monkeypatch.setattr(library, "MAX_EXTRACTED_CHARS", 4)
    char_dir = tmp_path / "chars"
    char_dir.mkdir()
    library.add_report(char_dir, "first.txt", b"four")
    with pytest.raises(ValueError, match="50 万字"):
        library.add_report(char_dir, "second.txt", b"x")


def test_enforces_total_pdf_page_quota(tmp_path: Path, monkeypatch) -> None:
    _fake_pdf_reader(monkeypatch, [None])
    monkeypatch.setattr(library, "MAX_PDF_PAGES", 1)
    library.add_report(tmp_path, "first.pdf", b"first pdf")

    with pytest.raises(ValueError, match="500 页"):
        library.add_report(tmp_path, "second.pdf", b"second pdf")


def test_rolls_back_new_object_when_index_replacement_fails(tmp_path: Path, monkeypatch) -> None:
    library.add_report(tmp_path, "existing.txt", b"previous report")
    index_path = tmp_path / "research_tools" / "library" / library._INDEX_NAME
    previous_index = index_path.read_bytes()
    real_replace = library.os.replace

    def fail_index_replace(source, destination):
        if Path(destination).name == library._INDEX_NAME:
            raise OSError("private contents must not appear in the error")
        real_replace(source, destination)

    monkeypatch.setattr(library.os, "replace", fail_index_replace)

    with pytest.raises(ValueError, match="资料库写入失败") as exc:
        library.add_report(tmp_path, "partial.txt", b"private text")

    assert "private contents" not in str(exc.value)
    objects = tmp_path / "research_tools" / "library" / "objects"
    assert len(list(objects.iterdir())) == 1
    assert index_path.read_bytes() == previous_index
    assert [item["name"] for item in library.list_reports(tmp_path)["items"]] == ["existing.txt"]


def test_rejects_symlinked_library_directory(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    library_parent = tmp_path / "research_tools"
    library_parent.mkdir()
    try:
        (library_parent / "library").symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(ValueError, match="符号链接"):
        library.list_reports(tmp_path)


def test_concurrent_adds_keep_index_complete(tmp_path: Path) -> None:
    def add_one(index: int) -> str:
        return library.add_report(tmp_path, f"report-{index}.txt", f"内容 {index}".encode())["item"]["id"]

    with ThreadPoolExecutor(max_workers=6) as pool:
        ids = list(pool.map(add_one, range(6)))

    reports = library.list_reports(tmp_path)["items"]
    assert {item["id"] for item in reports} == set(ids)
    assert len(reports) == 6
