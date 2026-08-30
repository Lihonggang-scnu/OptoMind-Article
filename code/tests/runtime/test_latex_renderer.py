"""T-14 tests: Unicode-safe QA gate over rendered PDF + markdown."""

from __future__ import annotations

import json

import pytest

from optomind_research.runtime import latex_publication_renderer as lpr
from optomind_research.runtime.latex_publication_renderer import (
    QAFailure,
    qa_check,
    run_qa_gate,
)


PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000d49444154789c6260000000060005"
    "27de3bbb0000000049454e44ae426082"
)


@pytest.fixture()
def pdf_factory(tmp_path, monkeypatch):
    """Create a dummy PDF file and mock the extraction seam."""
    def _make(extracted_text: str, *, embedded_font: bool = True):
        raw = b"%PDF-1.4 "
        if embedded_font:
            raw += b"/FontFile2 0 obj"
        else:
            raw += b"/Type /Font"
        pdf_path = tmp_path / "main.pdf"
        pdf_path.write_bytes(raw)
        monkeypatch.setattr(
            lpr, "_extract_pdf_text", lambda path: extracted_text
        )
        return pdf_path

    return _make


def _write_md(tmp_path, content, name="article.md"):
    md_path = tmp_path / name
    md_path.write_text(content, encoding="utf-8")
    return md_path


def test_coverage_below_threshold(pdf_factory, tmp_path):
    # multiset trick: 20 x's in md, 17 in pdf -> exactly 85% coverage
    pdf = pdf_factory("x" * 17)
    md = _write_md(tmp_path, "x" * 20)
    report = qa_check(pdf, md)
    assert abs(report.coverage_ratio - 0.85) < 1e-9
    assert report.coverage_passed is False
    assert report.qa_passed is False


def test_figures_missing(pdf_factory, tmp_path):
    text = "The certified margin stays above the charter floor."
    pdf = pdf_factory(text)
    md = _write_md(
        tmp_path,
        f"{text}\n\n![band structure](figures/foo.png)\n",
    )
    (tmp_path / "figures").mkdir()
    report = qa_check(pdf, md)
    assert report.figures_passed is False
    assert any("FIGURE_MISSING" in w for w in report.reference_warnings)
    assert report.qa_passed is False


def test_mojibake_detected(pdf_factory, tmp_path):
    body = "Reflectance stays flat across the window."
    pdf = pdf_factory(f"Ã‚Â{body}")
    md = _write_md(tmp_path, body)
    report = qa_check(pdf, md)
    assert report.mojibake_passed is False
    assert report.qa_passed is False


def test_valid_scientific_symbols_not_flagged(pdf_factory, tmp_path):
    body = (
        "The design uses SiO\\_2 and TiO\\_2 layers; the resonance sits at "
        "550 nm with a 30 deg incidence and wavelength lambda coverage of "
        "450-700 nm at 1 um scale."
    )
    pdf = pdf_factory(body)
    md = _write_md(tmp_path, body)
    report = qa_check(pdf, md)
    assert report.mojibake_passed is True


def test_qa_failure_writes_report(pdf_factory, tmp_path):
    pdf = pdf_factory("partial")  # low coverage -> failure
    md = _write_md(tmp_path, "a" * 40)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    with pytest.raises(QAFailure) as excinfo:
        run_qa_gate(pdf, md, out_dir)
    assert excinfo.value.report.qa_passed is False
    failure_file = out_dir / "QA_FAILURE_REPORT.json"
    assert failure_file.is_file()
    payload = json.loads(failure_file.read_text(encoding="utf-8"))
    assert payload["status"] == "qa_failure"
    assert payload["qa_passed"] is False
    assert payload["coverage_ratio"] < 0.9


def test_qa_passed_all_clear(pdf_factory, tmp_path):
    body = "Certified reflectance margins remain stable under the charter."
    pdf = pdf_factory(body)
    md = _write_md(tmp_path, body)
    report = qa_check(pdf, md)
    assert report.coverage_passed is True
    assert report.figures_passed is True
    assert report.mojibake_passed is True
    assert report.qa_passed is True
