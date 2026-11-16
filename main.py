import os
import time
from pathlib import Path

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential

from association.associate_documents import associate_batch
from rules.compliance_rules import run_compliance_checks
from Submission.multi_document_processor import process_file


# =========================================================
# Azure Document Intelligence configuration
# =========================================================

endpoint = os.environ.get(
    "AZURE_DOC_INTEL_ENDPOINT",
    "https://ro-doc-intelligence.cognitiveservices.azure.com/",
)
key = os.environ.get("AZURE_DOC_INTEL_KEY")

if not key:
    raise SystemExit(
        "Set the AZURE_DOC_INTEL_KEY environment variable first."
    )

client = DocumentIntelligenceClient(
    endpoint=endpoint,
    credential=AzureKeyCredential(key),
)


# =========================================================
# Dataset configuration
# =========================================================

LABEL_TO_GROUND_TRUTH = {
    "Compliant": "COMPLIANT",
    "Non-Compliant": "NON_COMPLIANT",
}


# =========================================================
# Azure document analysis
# =========================================================

def analyze_document(file_path):
    """Analyze one PDF with Azure Document Intelligence."""
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(
            f"Document was not found: {file_path.resolve()}"
        )

    for attempt in range(1, 4):
        try:
            with file_path.open("rb") as document_file:
                poller = client.begin_analyze_document(
                    "prebuilt-layout",
                    body=document_file,
                )
                return poller.result()

        except Exception as error:  # noqa: BLE001
            message = str(error)
            permanent_error = (
                "401" in message
                or "InvalidRequest" in message
            )

            if attempt == 3 or permanent_error:
                if "InvalidRequest" in message:
                    size_mb = file_path.stat().st_size / 1_000_000
                    raise RuntimeError(
                        "Azure rejected the file "
                        f"({size_mb:.1f} MB). The free Azure tier may "
                        "reject PDFs larger than 4 MB."
                    ) from error
                raise

            print(
                f"  Retrying {file_path.name} "
                f"(attempt {attempt + 1} of 3): "
                f"{message[:80]}"
            )
            time.sleep(5 * attempt)


# =========================================================
# Output helpers
# =========================================================

def get_record_source(record):
    return {
        "file": record.get("source_file"),
        "page": record.get("page_number"),
    }


def get_endorsement_output(record):
    schema = record.get("schema") or {}
    return {
        "file": record.get("source_file"),
        "page": record.get("page_number"),
        "named_insured": schema.get("named_insured"),
        "policy_number": schema.get("policy_number"),
        "carrier": (
            schema.get("endorsement_carrier")
            or schema.get("carrier")
            or schema.get("insurer")
            or schema.get("insurer_name")
            or schema.get("insurance_company")
        ),
        "form_number": schema.get("form_number"),
        "evidence_tags": (
            record.get("evidence_tags")
            or schema.get("evidence_tags")
            or []
        ),
    }


def get_endorsement_evidence(endorsement_schemas):
    evidence = set()
    for schema in endorsement_schemas:
        evidence.update(schema.get("evidence_tags", []))
    return sorted(evidence)


def get_acord_policy_numbers(acord_schema):
    policy_numbers = {}
    for coverage_name, coverage_data in (
        acord_schema.get("coverages") or {}
    ).items():
        if isinstance(coverage_data, dict):
            policy_numbers[coverage_name] = coverage_data.get(
                "policy_number"
            )
    return policy_numbers


def print_section(title, character="-"):
    print()
    print(character * 72)
    print(title)
    print(character * 72)


def print_value(label, value, indent=0):
    prefix = " " * indent
    display_value = (
        value if value not in (None, "", []) else "Not found"
    )
    print(f"{prefix}{label:<24} {display_value}")


def format_field_name(field_name):
    replacements = {
        "gl": "GL",
        "el": "E.L.",
        "acord": "ACORD",
        "noncontributory": "Non-Contributory",
    }
    parts = (
        str(field_name)
        .replace(".", " ")
        .replace("_", " ")
        .split()
    )
    return " ".join(
        replacements.get(part.lower(), part.capitalize())
        for part in parts
    )


def print_policy_numbers(policy_numbers):
    print("\n  POLICY NUMBERS")
    if not policy_numbers:
        print("    None found")
        return

    for coverage_name, policy_number in policy_numbers.items():
        print_value(
            format_field_name(coverage_name),
            policy_number or "Missing",
            indent=4,
        )


def print_endorsements(endorsements):
    print("\n  ASSOCIATED ENDORSEMENTS")
    if not endorsements:
        print("    None associated")
        return

    for index, endorsement in enumerate(endorsements, start=1):
        file_name = endorsement.get("file") or "Unknown file"
        page_number = endorsement.get("page")
        page_text = (
            f", page {page_number}"
            if page_number is not None
            else ""
        )
        print(f"\n    {index}. {file_name}{page_text}")
        print_value(
            "Form", endorsement.get("form_number"), indent=7
        )
        print_value(
            "Named insured",
            endorsement.get("named_insured"),
            indent=7,
        )
        print_value(
            "Policy number",
            endorsement.get("policy_number"),
            indent=7,
        )
        print_value(
            "Carrier", endorsement.get("carrier"), indent=7
        )

        tags = endorsement.get("evidence_tags", [])
        if tags:
            print("       Evidence:")
            for tag in tags:
                print(f"         - {format_field_name(tag)}")
        else:
            print("       Evidence: None detected")


def print_compliance_checks(checks):
    print("\n  COMPLIANCE RESULTS")
    if not checks:
        print("    No compliance checks available")
        return

    order = (
        "FAIL",
        "MISSING",
        "REVIEW_REQUIRED",
        "PASS",
        "NOT_APPLICABLE",
    )
    labels = {
        "FAIL": "[FAIL]",
        "MISSING": "[MISSING]",
        "REVIEW_REQUIRED": "[REVIEW]",
        "PASS": "[PASS]",
        "NOT_APPLICABLE": "[N/A]",
    }

    for status in order:
        matching = [
            (name, result)
            for name, result in checks.items()
            if result.get("status") == status
        ]
        if not matching:
            continue

        print(f"\n    {labels[status]} {status} ({len(matching)})")
        for name, result in matching:
            print(f"      - {format_field_name(name)}")
            if result.get("reason"):
                print(f"        {result['reason']}")
            if result.get("required") is not None:
                print(f"        Required: {result['required']:,}")
            if result.get("found") is not None:
                print(f"        Found:    {result['found']:,}")


def print_association_details(association_batch):
    unresolved = association_batch.get("unassociated_records", [])
    print_section("ASSOCIATION DETAILS")

    if not unresolved:
        print(
            "All ACORD and endorsement records were assigned "
            "to a submission group."
        )
        return

    print(f"Unresolved documents: {len(unresolved)}")
    for index, item in enumerate(unresolved, start=1):
        record = item.get("record") or {}
        print(
            f"\n  {index}. "
            f"{record.get('source_file') or 'Unknown file'}"
        )
        print_value("Page", record.get("page_number"), indent=5)
        print_value("Reason", item.get("reason"), indent=5)


def print_final_package(package):
    print_section(
        f"SUBMISSION: {package['submission_id']}",
        character="=",
    )
    print_value("Package", package.get("company_name"))
    print_value("Group type", package.get("group_type"))
    print_value("Named insured", package.get("named_insured"))
    if package.get("carrier"):
        print_value("Carrier", package.get("carrier"))
    print_value("Overall status", package.get("overall_status"))

    source_acord = package.get("source_acord")
    if source_acord:
        source_text = (
            f"{source_acord.get('file')} | "
            f"page {source_acord.get('page')}"
        )
        print_value("ACORD source", source_text)
    else:
        print_value("ACORD source", "NOT FOUND")
        missing = package.get("missing_documents", [])
        if missing:
            print_value("Missing documents", ", ".join(missing))

    print_policy_numbers(package.get("policy_numbers", {}))
    print_endorsements(package.get("associated_endorsements", []))

    evidence = package.get("evidence", [])
    print("\n  COMBINED EVIDENCE")
    if evidence:
        for tag in evidence:
            print(f"    - {format_field_name(tag)}")
    else:
        print("    None detected")

    print_compliance_checks(package.get("checks", {}))


# =========================================================
# Submission-package builders
# =========================================================

def build_acord_package(
    company_name,
    company_folder,
    submission,
):
    acord_record = submission["acord_record"]
    acord_schema = acord_record.get("schema") or {}
    endorsement_records = submission.get("endorsement_records", [])
    endorsement_schemas = [
        record.get("schema") or {}
        for record in endorsement_records
    ]

    print("\n  DEBUG ENDORSEMENT SEMANTICS")

    for schema in endorsement_schemas:
        print(
            f"    Form: "
            f"{schema.get('form_number') or 'UNKNOWN'}"
        )

        print(
            f"      Additional Insured: "
            f"{schema.get('additional_insured_status')}"
        )

        print(
            f"      Ongoing: "
            f"{schema.get('ongoing_operations_status')}"
        )

        print(
            f"      Completed: "
            f"{schema.get('completed_operations_status')}"
        )

        print(
            f"      Primary: "
            f"{schema.get('primary_status')}"
        )

        print(
            f"      Noncontributory: "
            f"{schema.get('noncontributory_status')}"
        )

        print(
            f"      Negligence trigger: "
            f"{schema.get('negligence_trigger_status')}"
        )

        print(
            f"      Direct contract: "
            f"{schema.get('direct_contract_required')}"
        )

        print(
            f"      Restrictions: "
            f"{schema.get('material_restrictions')}"
        )


        if schema.get("material_restrictions"):
            matching_record = next(
                (
                    record
                    for record in endorsement_records
                    if (
                        (record.get("schema") or {}).get(
                            "form_number"
                        )
                        == schema.get("form_number")
                    )
                ),
                None,
            )

            if matching_record:
                text = (
                    matching_record.get("full_text")
                    or matching_record.get("raw_text")
                    or ""
                )

                print("      RESTRICTION TEXT:")

                upper_text = text.upper()

                for phrase in (
                    "SOLE NEGLIGENCE",
                    "PROFESSIONAL",
                    "ARCHITECTURAL",
                    "ENGINEERING",
                    "SURVEYING",
                ):
                    position = upper_text.find(phrase)

                    if position >= 0:
                        start = max(0, position - 500)
                        end = min(
                            len(text),
                            position + 700,
                        )

                        print(
                            f"        [{phrase}] "
                            f"{text[start:end]}"
                        )

    compliance_results = run_compliance_checks(
        acord_schema,
        endorsement_schemas,
    )

    submission_id = (
        f"{company_name}-{submission['submission_id']}"
    )

    return {
        "company_name": company_name,
        "company_folder": str(company_folder),
        "submission_id": submission_id,
        "group_type": "ACORD_BASED",
        "overall_status": compliance_results["overall_status"],
        "source_acord": get_record_source(acord_record),
        "associated_endorsements": [
            get_endorsement_output(record)
            for record in endorsement_records
        ],
        "named_insured": acord_schema.get("named_insured"),
        "policy_numbers": get_acord_policy_numbers(acord_schema),
        "carrier": None,
        "missing_documents": [],
        "evidence": compliance_results["combined_evidence"],
        "checks": compliance_results["checks"],
        "association_results": submission.get(
            "association_results", []
        ),
        "rule_set": compliance_results.get("rule_set"),
        "review_date": compliance_results.get("review_date"),
    }


def build_endorsement_only_package(
    company_name,
    company_folder,
    submission,
):
    endorsement_records = submission.get("endorsement_records", [])
    endorsement_schemas = [
        record.get("schema") or {}
        for record in endorsement_records
    ]

    submission_id = (
        f"{company_name}-{submission['submission_id']}"
    )
    reason = (
        "Related endorsements were grouped, but no matching "
        "ACORD 25 certificate was found."
    )

    return {
        "company_name": company_name,
        "company_folder": str(company_folder),
        "submission_id": submission_id,
        "group_type": "ENDORSEMENT_ONLY",
        "overall_status": "INCOMPLETE_SUBMISSION",
        "source_acord": None,
        "associated_endorsements": [
            get_endorsement_output(record)
            for record in endorsement_records
        ],
        "named_insured": submission.get("named_insured"),
        "policy_numbers": {
            "endorsement_policy": submission.get("policy_number")
        },
        "carrier": submission.get("carrier"),
        "missing_documents": ["ACORD_25"],
        "evidence": get_endorsement_evidence(endorsement_schemas),
        "checks": {
            "submission_documents": {
                "status": "MISSING",
                "reason": reason,
            }
        },
        "association_results": submission.get(
            "association_results", []
        ),
    }


def build_submission_package(
    company_name,
    company_folder,
    submission,
):
    group_type = submission.get("group_type", "ACORD_BASED")
    acord_record = submission.get("acord_record")

    if group_type == "ACORD_BASED" and acord_record is not None:
        return build_acord_package(
            company_name,
            company_folder,
            submission,
        )

    return build_endorsement_only_package(
        company_name,
        company_folder,
        submission,
    )


# =========================================================
# Labeled evaluation helpers
# =========================================================

def determine_package_status(packages):
    """Roll submission statuses into one PDF-package prediction."""
    statuses = {
        package.get("overall_status")
        for package in packages
        if package.get("overall_status")
    }

    if "NON_COMPLIANT" in statuses:
        return "NON_COMPLIANT"
    if "INCOMPLETE_SUBMISSION" in statuses:
        return "INCOMPLETE_SUBMISSION"
    if "HUMAN_REVIEW_REQUIRED" in statuses:
        return "HUMAN_REVIEW_REQUIRED"
    if statuses == {"COMPLIANT"}:
        return "COMPLIANT"
    return "HUMAN_REVIEW_REQUIRED"


def prediction_matches_ground_truth(
    ground_truth,
    predicted_status,
):
    return ground_truth == predicted_status


def process_labeled_package(pdf_path, label_name):
    """Run one PDF independently and compare only after prediction."""
    ground_truth = LABEL_TO_GROUND_TRUTH[label_name]

    print_section(
        f"PROCESSING PACKAGE: {pdf_path.name}",
        character="=",
    )
    print_value("Ground truth", ground_truth)

    # The ground-truth folder label is not passed into the pipeline.
    page_records = process_file(pdf_path, analyze_document)

    print("\n  DEBUG RECORD TYPES")

    for record in page_records:
        schema = record.get("schema") or {}

        print(
            f"    Page {record.get('page_number')}: "
            f"{schema.get('document_type')}"
        )

        print(
            f"      Page type: "
            f"{record.get('page_type')}"
        )

        print(
            f"      Evidence tags: "
            f"{record.get('evidence_tags') or schema.get('evidence_tags')}"
        )

        print(
            f"      Form number: "
            f"{schema.get('form_number')}"
        )

        print(
            f"      Extracted text: "
            f"{str(
                schema.get('raw_text')
                or schema.get('additional_remarks')
                or record.get('raw_text')
                or ''
            )[:1500]}"
        )

    print_section("INTAKE SUMMARY")
    print_value("PDF package", pdf_path.name)
    print_value("Pages/records processed", len(page_records))

    if not page_records:
        return {
            "file_name": pdf_path.name,
            "file_path": str(pdf_path),
            "ground_truth": ground_truth,
            "predicted_status": "INCOMPLETE_SUBMISSION",
            "correct": False,
            "status": "NO_SUPPORTED_RECORDS",
            "submissions": [],
            "unassociated_records": [],
            "other_records": [],
        }

    association_batch = associate_batch(page_records)

    print_section("ASSOCIATION SUMMARY")
    print_value(
        "ACORD 25 pages",
        association_batch.get("acord_count", 0),
    )
    print_value(
        "Endorsement pages",
        association_batch.get("endorsement_count", 0),
    )
    print_value(
        "Submission groups",
        len(association_batch.get("submissions", [])),
    )
    print_value(
        "Unresolved pages",
        len(association_batch.get("unassociated_records", [])),
    )
    print_association_details(association_batch)

    packages = []
    for submission in association_batch.get("submissions", []):
        package = build_submission_package(
            pdf_path.stem,
            pdf_path.parent,
            submission,
        )
        packages.append(package)
        print_final_package(package)

    predicted_status = determine_package_status(packages)
    correct = prediction_matches_ground_truth(
        ground_truth,
        predicted_status,
    )

    print_section("LABELED EVALUATION RESULT")
    print_value("PDF package", pdf_path.name)
    print_value("Ground truth", ground_truth)
    print_value("System prediction", predicted_status)
    print_value("Exact match", "YES" if correct else "NO")

    return {
        "file_name": pdf_path.name,
        "file_path": str(pdf_path),
        "ground_truth": ground_truth,
        "predicted_status": predicted_status,
        "correct": correct,
        "status": "PROCESSED",
        "submissions": packages,
        "unassociated_records": association_batch.get(
            "unassociated_records", []
        ),
        "other_records": association_batch.get("other_records", []),
    }


# =========================================================
# Main processing
# =========================================================

def main():
    documents_root = (
        Path(__file__).parent
        / "Sample_Documents"
        / "Subcontractor Insurance Packages"
    )

    if not documents_root.exists():
        raise FileNotFoundError(
            "Labeled subcontractor dataset was not found: "
            f"{documents_root.resolve()}"
        )

    print_section(
        "LABELED INSURANCE PACKAGE EVALUATION",
        character="=",
    )
    print_value("Dataset folder", documents_root.resolve())

    evaluation_results = []

    for label_name, ground_truth in LABEL_TO_GROUND_TRUTH.items():
        label_folder = documents_root / label_name

        if not label_folder.exists():
            print(f"\nSkipping missing label folder: {label_folder}")
            continue

        pdf_files = sorted(
            path
            for path in label_folder.rglob("*")
            if path.is_file() and path.suffix.lower() == ".pdf"
        )

        # For a two-package trial, temporarily uncomment this line.
        #pdf_files = pdf_files[:1]

        print_section(
            f"GROUND-TRUTH GROUP: {label_name}",
            character="=",
        )
        print_value("Ground truth", ground_truth)
        print_value("PDF packages found", len(pdf_files))

        for pdf_path in pdf_files:
            try:
                result = process_labeled_package(
                    pdf_path,
                    label_name,
                )
            except Exception as error:  # noqa: BLE001
                print_section(
                    f"PACKAGE ERROR: {pdf_path.name}",
                    character="!",
                )
                print_value("Error", str(error))
                result = {
                    "file_name": pdf_path.name,
                    "file_path": str(pdf_path),
                    "ground_truth": ground_truth,
                    "predicted_status": None,
                    "correct": False,
                    "status": "PROCESSING_ERROR",
                    "error": str(error),
                    "submissions": [],
                    "unassociated_records": [],
                    "other_records": [],
                }

            evaluation_results.append(result)

    print_section(
        "LABELED DATASET EVALUATION SUMMARY",
        character="=",
    )

    processed = [
        result
        for result in evaluation_results
        if result.get("status") == "PROCESSED"
    ]
    errors = [
        result
        for result in evaluation_results
        if result.get("status") == "PROCESSING_ERROR"
    ]
    exact_matches = [
        result for result in processed if result.get("correct")
    ]

    total_processed = len(processed)
    exact_accuracy = (
        len(exact_matches) / total_processed
        if total_processed
        else 0.0
    )

    print_value("Packages discovered", len(evaluation_results))
    print_value("Packages processed", total_processed)
    print_value("Processing errors", len(errors))
    print_value("Exact binary matches", len(exact_matches))
    print_value("Exact accuracy", f"{exact_accuracy:.1%}")

    print("\n  SYSTEM OUTPUTS")
    for status in (
        "COMPLIANT",
        "NON_COMPLIANT",
        "HUMAN_REVIEW_REQUIRED",
        "INCOMPLETE_SUBMISSION",
    ):
        count = sum(
            1
            for result in processed
            if result.get("predicted_status") == status
        )
        print_value(format_field_name(status), count, indent=4)

    print("\n  PER-PACKAGE RESULTS")
    for result in evaluation_results:
        predicted = (
            result.get("predicted_status")
            or result.get("status")
        )
        marker = "MATCH" if result.get("correct") else "CHECK"
        print(f"\n    [{marker}] {result['file_name']}")
        print_value(
            "Ground truth",
            result.get("ground_truth"),
            indent=6,
        )
        print_value("Prediction", predicted, indent=6)
        if result.get("error"):
            print_value("Error", result["error"], indent=6)


if __name__ == "__main__":
    main()
