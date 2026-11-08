"""
Regression tests on what Azure Document Intelligence actually returned for
two sample certificates (saved with dump_azure.py, Nov 7 2026).
Values were checked by eye against the PDFs.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from Submission.multi_document_processor import process_file  # noqa: E402
from association.associate_documents import associate_batch  # noqa: E402
from rules.compliance_rules import run_compliance_checks  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


def azure_result(name):
    """Rebuilds an Azure-like result object from a dump_azure.py file."""

    data = json.loads((FIXTURES / name).read_text())
    pages = []

    for page in data["pages"]:
        lines = [
            SimpleNamespace(content=line["text"], polygon=line["polygon"])
            for line in page["line_polygons"]
        ]
        marks = [
            SimpleNamespace(state=mark["state"], polygon=mark["polygon"])
            for mark in page["selection_marks"]
        ]
        pages.append(SimpleNamespace(
            page_number=page["page_number"], lines=lines,
            selection_marks=marks))

    return SimpleNamespace(pages=pages)


def records_for(name, tmp_path):
    fake_pdf = tmp_path / name.replace(".azure.json", ".pdf")
    fake_pdf.write_bytes(b"")
    return process_file(fake_pdf, lambda _: azure_result(name))


def test_acuity_certificate_values(tmp_path):
    acord = records_for("acuity_cb7242.azure.json", tmp_path)[0]["schema"]
    cov = acord["coverages"]

    assert acord["extraction_method"] == "layout"
    assert acord["certificate_date"] == "2017-06-08"
    assert cov["general_liability"]["each_occurrence"] == "1000000"
    assert cov["general_liability"]["general_aggregate"] == "3000000"
    assert cov["general_liability"]["products_completed_operations"] == "3000000"
    assert cov["general_liability"]["occurrence_form"] == "OCCURRENCE"
    assert cov["general_liability"]["aggregate_applies_per"] == "PROJECT"
    assert cov["general_liability"]["expiration_date"] == "2017-12-10"
    assert cov["automobile_liability"]["combined_single_limit"] == "1000000"
    assert cov["umbrella_excess"]["each_occurrence"] == "9000000"
    assert cov["workers_compensation"]["each_accident"] == "500000"
    assert cov["workers_compensation"]["disease_policy_limit"] == "500000"
    assert cov["workers_compensation"]["per_statute"] is True
    assert acord["insurers"]["insurer_a"] == "Acuity"
    assert acord["certificate_holder"].startswith("Kier Construction")
    assert acord["certificate_holder_check"] == "MISMATCH"


def test_ace_certificate_values(tmp_path):
    records = records_for("ace_env3101.azure.json", tmp_path)
    acord = records[0]["schema"]
    cov = acord["coverages"]

    assert cov["general_liability"]["policy_number"] == "G24324722005"
    assert cov["general_liability"]["each_occurrence"] == "2000000"
    assert cov["general_liability"]["general_aggregate"] == "2000000"
    assert cov["automobile_liability"]["policy_number"] == "52UECNK1857DX"
    assert cov["automobile_liability"]["combined_single_limit"] == "1000000"
    assert cov["umbrella_excess"]["policy_number"] == "G27160712004"
    assert cov["workers_compensation"]["each_accident"] is None
    assert acord["insurers"] == {
        "insurer_a": "Westchester Surplus Lines Insurance",
        "insurer_b": "Sentinel Insurance Company, Ltd.",
    }

    # Page 2 is the waiver endorsement for the same GL policy.
    endorsement = records[1]["schema"]
    assert endorsement["form_number"].startswith("ENV 3143")
    assert endorsement["policy_number"] == "G24324722005"


def test_ace_end_to_end(tmp_path):
    records = records_for("ace_env3101.azure.json", tmp_path)
    batch = associate_batch(records)

    assert len(batch["submissions"]) == 1
    submission = batch["submissions"][0]
    assert len(submission["endorsement_records"]) == 1

    result = run_compliance_checks(
        submission["acord_record"]["schema"],
        [e["schema"] for e in submission["endorsement_records"]],
        review_date="2017-01-15",
    )
    checks = result["checks"]

    assert checks["general_liability.each_occurrence"]["status"] == "PASS"
    assert checks["waiver_of_subrogation"]["status"] == "PASS"
    # Certificate says "additional insured per attached form ENV3101",
    # but that form is not in the package, so AI is not proven.
    assert checks["additional_insured"]["status"] == "MISSING"
    assert checks["certificate_holder"]["status"] == "FAIL"
    assert result["overall_status"] != "COMPLIANT"


def test_checkbox_state_as_sdk_enum(tmp_path):
    """The Azure SDK reports checkbox state as an enum, not plain text."""

    from enum import Enum

    class DocumentSelectionMarkState(str, Enum):
        SELECTED = "selected"
        UNSELECTED = "unselected"

    result = azure_result("ace_env3101.azure.json")

    for page in result.pages:
        for mark in page.selection_marks:
            mark.state = DocumentSelectionMarkState(mark.state)

    fake_pdf = tmp_path / "ace.pdf"
    fake_pdf.write_bytes(b"")
    acord = process_file(fake_pdf, lambda _: result)[0]["schema"]
    gl = acord["coverages"]["general_liability"]

    assert gl["occurrence_form"] == "OCCURRENCE"
    assert gl["aggregate_applies_per"] == "PROJECT"
    assert acord["coverages"]["automobile_liability"]["any_auto"] is True


def test_review_date_and_missing_workers_comp(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEW_DATE", "2017-01-15")
    records = records_for("ace_env3101.azure.json", tmp_path)
    batch = associate_batch(records)
    submission = batch["submissions"][0]

    result = run_compliance_checks(
        submission["acord_record"]["schema"],
        [e["schema"] for e in submission["endorsement_records"]],
    )
    checks = result["checks"]

    assert result["review_date"] == "2017-01-15"
    assert checks["policy_dates.general_liability"]["status"] == "PASS"
    assert checks["employers_liability.each_accident"]["status"] == "MISSING"
    assert "policy_dates.workers_compensation" not in checks
