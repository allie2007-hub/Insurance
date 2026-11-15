import os
import time
from pathlib import Path

from azure.ai.documentintelligence import (
    DocumentIntelligenceClient
)
from azure.core.credentials import (
    AzureKeyCredential
)

from association.associate_documents import (
    associate_batch
)
from rules.compliance_rules import (
    run_compliance_checks
)
from Submission.multi_document_processor import (
    process_intake_folder
)


# =========================================================
# Azure Document Intelligence configuration
# =========================================================

# Never place the Azure key directly in this file.
#
# PowerShell:
# $env:AZURE_DOC_INTEL_KEY="your-key"
#
# Optional custom endpoint:
# $env:AZURE_DOC_INTEL_ENDPOINT="https://your-resource.cognitiveservices.azure.com/"

endpoint = os.environ.get(
    "AZURE_DOC_INTEL_ENDPOINT",
    "https://ro-doc-intelligence.cognitiveservices.azure.com/"
)

key = os.environ.get(
    "AZURE_DOC_INTEL_KEY"
)

if not key:
    raise SystemExit(
        "Set the AZURE_DOC_INTEL_KEY "
        "environment variable first."
    )


client = DocumentIntelligenceClient(
    endpoint=endpoint,
    credential=AzureKeyCredential(key)
)


# =========================================================
# Azure document analysis
# =========================================================

def analyze_document(file_path):
    """
    Sends one PDF to Azure Document Intelligence and
    returns the analysis result.

    Temporary connection failures and HTTP 429 rate-limit
    errors may be retried up to three times.
    """

    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(
            "Document was not found: "
            f"{file_path.resolve()}"
        )

    for attempt in range(1, 4):

        try:
            with file_path.open(
                "rb"
            ) as document_file:

                poller = (
                    client.begin_analyze_document(
                        "prebuilt-layout",
                        body=document_file
                    )
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

                    size_mb = (
                        file_path.stat().st_size
                        / 1_000_000
                    )

                    raise RuntimeError(
                        "Azure rejected the file "
                        f"({size_mb:.1f} MB). "
                        "The free Azure tier may reject "
                        "PDFs larger than 4 MB."
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
    """
    Returns a consistent source description for a page record.
    """

    return {
        "file": record.get("source_file"),
        "page": record.get("page_number"),
    }


def get_endorsement_output(record):
    """
    Creates the output summary for one associated endorsement.
    """

    schema = record.get("schema") or {}

    return {
        "file":
            record.get("source_file"),
        "page":
            record.get("page_number"),
        "named_insured":
            schema.get("named_insured"),
        "policy_number":
            schema.get("policy_number"),
        "carrier": (
            schema.get("endorsement_carrier")
            or schema.get("carrier")
            or schema.get("insurer")
            or schema.get("insurer_name")
            or schema.get("insurance_company")
        ),
        "form_number":
            schema.get("form_number"),
        "evidence_tags":
            record.get("evidence_tags")
            or schema.get("evidence_tags")
            or [],
    }


def get_endorsement_evidence(
    endorsement_schemas
):
    """
    Combines evidence tags from an endorsement-only group.
    """

    evidence = set()

    for schema in endorsement_schemas:
        evidence.update(
            schema.get(
                "evidence_tags",
                []
            )
        )

    return sorted(evidence)


def get_acord_policy_numbers(
    acord_schema
):
    """
    Returns policy numbers organized by coverage name.
    """

    policy_numbers = {}

    coverages = (
        acord_schema.get("coverages")
        or {}
    )

    for (
        coverage_name,
        coverage_data
    ) in coverages.items():

        if not isinstance(
            coverage_data,
            dict
        ):
            continue

        policy_numbers[
            coverage_name
        ] = coverage_data.get(
            "policy_number"
        )

    return policy_numbers


def print_section(title, character="-"):
    """Prints a clear terminal section heading."""
    print()
    print(character * 72)
    print(title)
    print(character * 72)


def print_value(label, value, indent=0):
    """Prints a readable label/value pair."""
    prefix = " " * indent
    display_value = value if value not in (None, "", []) else "Not found"
    print(f"{prefix}{label:<24} {display_value}")


def format_field_name(field_name):
    """Converts internal field names into readable terminal labels."""
    replacements = {
        "gl": "GL",
        "el": "E.L.",
        "acord": "ACORD",
        "noncontributory": "Non-Contributory",
    }
    parts = str(field_name).replace(".", " ").replace("_", " ").split()
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
            indent=4
        )


def print_endorsements(endorsements):
    print("\n  ASSOCIATED ENDORSEMENTS")
    if not endorsements:
        print("    None associated")
        return

    for index, endorsement in enumerate(endorsements, start=1):
        file_name = endorsement.get("file") or "Unknown file"
        page_number = endorsement.get("page")
        page_text = f", page {page_number}" if page_number is not None else ""
        print(f"\n    {index}. {file_name}{page_text}")
        print_value("Form", endorsement.get("form_number"), indent=7)
        print_value("Named insured", endorsement.get("named_insured"), indent=7)
        print_value("Policy number", endorsement.get("policy_number"), indent=7)
        print_value("Carrier", endorsement.get("carrier"), indent=7)

        tags = endorsement.get("evidence_tags", [])
        if tags:
            print("       Evidence:")
            for tag in tags:
                print(f"         - {format_field_name(tag)}")
        else:
            print("       Evidence: None detected")


def print_compliance_checks(checks):
    """Prints compliance checks grouped by status instead of one huge dict."""
    print("\n  COMPLIANCE RESULTS")
    if not checks:
        print("    No compliance checks available")
        return

    order = ("FAIL", "MISSING", "REVIEW_REQUIRED", "PASS")
    labels = {
        "FAIL": "[FAIL]",
        "MISSING": "[MISSING]",
        "REVIEW_REQUIRED": "[REVIEW]",
        "PASS": "[PASS]",
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
            reason = result.get("reason")
            if reason:
                print(f"        {reason}")
            required = result.get("required")
            found = result.get("found")
            if required is not None:
                print(f"        Required: {required:,}")
            if found is not None:
                print(f"        Found:    {found:,}")


def print_association_details(association_batch):
    """Prints only documents that remain unresolved after grouping."""
    unresolved = association_batch.get("unassociated_records", [])
    print_section("ASSOCIATION DETAILS")

    if not unresolved:
        print("All ACORD and endorsement records were assigned to a submission group.")
        return

    print(f"Unresolved documents: {len(unresolved)}")
    for index, item in enumerate(unresolved, start=1):
        record = item.get("record") or {}
        print(f"\n  {index}. {record.get('source_file') or 'Unknown file'}")
        print_value("Page", record.get("page_number"), indent=5)
        print_value("Reason", item.get("reason"), indent=5)
        if item.get("best_score") is not None:
            print_value("Best score", item.get("best_score"), indent=5)
        if item.get("best_confidence") is not None:
            print_value("Best confidence", item.get("best_confidence"), indent=5)
        if item.get("best_reasons"):
            print("     Supporting evidence:")
            for reason in item["best_reasons"]:
                print(f"       + {reason}")
        if item.get("conflicts"):
            print("     Conflicting evidence:")
            for conflict in item["conflicts"]:
                print(f"       - {conflict}")


def print_final_package(package):
    """Prints one submission package in a compact, readable format."""
    print_section(f"SUBMISSION: {package['submission_id']}", character="=")
    print_value("Carrier folder", package.get("company_name"))
    print_value("Group type", package.get("group_type"))
    print_value("Named insured", package.get("named_insured"))
    if package.get("carrier"):
        print_value("Carrier", package.get("carrier"))
    print_value("Overall status", package.get("overall_status"))

    source_acord = package.get("source_acord")
    if source_acord:
        source_text = f"{source_acord.get('file')} | page {source_acord.get('page')}"
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
    submission
):
    """
    Builds a complete ACORD-based submission package and
    runs the R&O compliance checks.
    """

    acord_record = submission[
        "acord_record"
    ]

    acord_schema = (
        acord_record.get("schema")
        or {}
    )

    endorsement_records = submission.get(
        "endorsement_records",
        []
    )

    endorsement_schemas = [
        record.get("schema") or {}
        for record in endorsement_records
    ]

    compliance_results = (
        run_compliance_checks(
            acord_schema,
            endorsement_schemas
        )
    )

    submission_id = (
        f"{company_name}-"
        f"{submission['submission_id']}"
    )

    return {
        "company_name":
            company_name,
        "company_folder":
            str(company_folder),
        "submission_id":
            submission_id,
        "group_type":
            "ACORD_BASED",
        "overall_status":
            compliance_results[
                "overall_status"
            ],
        "source_acord":
            get_record_source(
                acord_record
            ),
        "associated_endorsements": [
            get_endorsement_output(
                record
            )
            for record
            in endorsement_records
        ],
        "named_insured":
            acord_schema.get(
                "named_insured"
            ),
        "policy_numbers":
            get_acord_policy_numbers(
                acord_schema
            ),
        "carrier":
            None,
        "missing_documents": [],
        "evidence":
            compliance_results[
                "combined_evidence"
            ],
        "checks":
            compliance_results[
                "checks"
            ],
        "association_results":
            submission.get(
                "association_results",
                []
            ),
        "rule_set":
            compliance_results.get(
                "rule_set"
            ),
        "review_date":
            compliance_results.get(
                "review_date"
            ),
    }


def build_endorsement_only_package(
    company_name,
    company_folder,
    submission
):
    """
    Builds an incomplete package when related endorsements
    were grouped but no corresponding ACORD was found.

    Full compliance is not run because certificate limits,
    coverage rows, certificate holder, and other ACORD
    evidence are unavailable.
    """

    endorsement_records = submission.get(
        "endorsement_records",
        []
    )

    endorsement_schemas = [
        record.get("schema") or {}
        for record in endorsement_records
    ]

    submission_id = (
        f"{company_name}-"
        f"{submission['submission_id']}"
    )

    reason = (
        "Related endorsements were grouped "
        "using insured, policy number, carrier, "
        "policy period, and document relationships, "
        "but no matching ACORD 25 certificate "
        "was found."
    )

    return {
        "company_name":
            company_name,
        "company_folder":
            str(company_folder),
        "submission_id":
            submission_id,
        "group_type":
            "ENDORSEMENT_ONLY",
        "overall_status":
            "INCOMPLETE_SUBMISSION",
        "source_acord":
            None,
        "associated_endorsements": [
            get_endorsement_output(
                record
            )
            for record
            in endorsement_records
        ],
        "named_insured":
            submission.get(
                "named_insured"
            ),
        "policy_numbers": {
            "endorsement_policy":
                submission.get(
                    "policy_number"
                )
        },
        "carrier":
            submission.get(
                "carrier"
            ),
        "missing_documents": [
            "ACORD_25"
        ],
        "evidence":
            get_endorsement_evidence(
                endorsement_schemas
            ),
        "checks": {
            "submission_documents": {
                "status": "MISSING",
                "reason": reason,
            }
        },
        "association_results":
            submission.get(
                "association_results",
                []
            ),
    }


def build_submission_package(
    company_name,
    company_folder,
    submission
):
    """
    Routes each association group to the correct package builder.
    """

    group_type = submission.get(
        "group_type",
        "ACORD_BASED"
    )

    acord_record = submission.get(
        "acord_record"
    )

    if (
        group_type == "ACORD_BASED"
        and acord_record is not None
    ):
        return build_acord_package(
            company_name,
            company_folder,
            submission
        )

    return build_endorsement_only_package(
        company_name,
        company_folder,
        submission
    )


# =========================================================
# Main processing
# =========================================================

def main():

    documents_root = (
        Path(__file__).parent
        / "Sample_Documents"
        / "Endorsements"
        / "Endorsements"
    )

    if not documents_root.exists():
        raise FileNotFoundError(
            "Sample documents folder not found: "
            f"{documents_root.resolve()}"
        )

    company_folders = sorted([
        folder
        for folder
        in documents_root.iterdir()
        if folder.is_dir()
    ])

    print_section("INSURANCE REVIEW PIPELINE", character="=")
    print_value("Carrier folders found", len(company_folders))

    all_company_results = []

    for company_folder in company_folders:

        company_name = company_folder.name

        print_section(
            f"PROCESSING CARRIER FOLDER: {company_name}",
            character="="
        )

        intake_result = process_intake_folder(
            company_folder,
            analyze_document
        )

        page_records = intake_result.get(
            "page_records",
            []
        )

        print_section("INTAKE SUMMARY")
        print_value("Carrier folder", company_name)
        print_value("Files found", intake_result.get("files_found", 0))
        print_value("Pages processed", len(page_records))
        print_value("File errors", len(intake_result.get("file_errors", [])))

        # -------------------------------------------------
        # Empty folder or no supported records
        # -------------------------------------------------

        if not page_records:

            all_company_results.append({
                "company_name":
                    company_name,
                "status":
                    "NO_SUPPORTED_FILES",
                "files_found":
                    intake_result.get(
                        "files_found",
                        0
                    ),
                "file_errors":
                    intake_result.get(
                        "file_errors",
                        []
                    ),
                "submissions": [],
                "unassociated_records": [],
                "other_records": [],
            })

            continue

        # -------------------------------------------------
        # Association and submission grouping
        # -------------------------------------------------

        association_batch = associate_batch(
            page_records
        )

        print_section("ASSOCIATION SUMMARY")
        print_value("ACORD 25 pages", association_batch.get("acord_count", 0))
        print_value("Endorsement pages", association_batch.get("endorsement_count", 0))
        print_value("Submission groups", len(association_batch.get("submissions", [])))
        print_value("ACORD-based groups", association_batch.get("acord_submission_count", 0))
        print_value("Endorsement-only groups", association_batch.get("endorsement_only_group_count", 0))
        print_value("Unresolved pages", len(association_batch.get("unassociated_records", [])))

        print_association_details(
            association_batch
        )

        # -------------------------------------------------
        # Build final submission packages
        # -------------------------------------------------

        company_packages = []

        for submission in (
            association_batch.get(
                "submissions",
                []
            )
        ):
            final_package = (
                build_submission_package(
                    company_name,
                    company_folder,
                    submission
                )
            )

            company_packages.append(
                final_package
            )

        company_result = {
            "company_name":
                company_name,
            "status":
                "PROCESSED",
            "files_found":
                intake_result.get(
                    "files_found",
                    0
                ),
            "pages_processed":
                len(page_records),
            "file_errors":
                intake_result.get(
                    "file_errors",
                    []
                ),
            "submissions":
                company_packages,
            "unassociated_records":
                association_batch.get(
                    "unassociated_records",
                    []
                ),
            "other_records":
                association_batch.get(
                    "other_records",
                    []
                ),
        }

        all_company_results.append(
            company_result
        )

        # -------------------------------------------------
        # Print final packages
        # -------------------------------------------------

        print_section("FINAL SUBMISSION PACKAGES", character="=")

        if not company_packages:
            print(
                "No submission package "
                "could be created."
            )

        for package in company_packages:
            print_final_package(package)

        # -------------------------------------------------
        # Print genuinely unresolved documents
        # -------------------------------------------------

        unassociated_records = (
            association_batch.get(
                "unassociated_records",
                []
            )
        )

        if unassociated_records:

            print_section("HUMAN REVIEW REQUIRED: UNRESOLVED DOCUMENTS", character="!")
            for index, item in enumerate(unassociated_records, start=1):
                record = item.get("record") or {}
                print(f"\n  {index}. {record.get('source_file') or 'Unknown file'}")
                print_value("Page", record.get("page_number"), indent=5)
                print_value("Reason", item.get("reason"), indent=5)

    # =====================================================
    # All-folder summary
    # =====================================================

    total_submissions = sum(
        len(company.get("submissions", []))
        for company in all_company_results
    )
    acord_based_total = sum(
        1
        for company in all_company_results
        for package in company.get("submissions", [])
        if package.get("group_type") == "ACORD_BASED"
    )
    endorsement_only_total = sum(
        1
        for company in all_company_results
        for package in company.get("submissions", [])
        if package.get("group_type") == "ENDORSEMENT_ONLY"
    )
    unresolved_total = sum(
        len(company.get("unassociated_records", []))
        for company in all_company_results
    )

    print_section("ALL-CARRIER-FOLDER PROCESSING SUMMARY", character="=")
    print_value("Folders processed", len(all_company_results))
    print_value("Submission groups", total_submissions)
    print_value("ACORD-based groups", acord_based_total)
    print_value("Endorsement-only groups", endorsement_only_total)
    print_value("Unresolved documents", unresolved_total)

    print("\n  PER-FOLDER SUMMARY")
    for company in all_company_results:
        submissions = company.get("submissions", [])
        acord_count = sum(
            1 for package in submissions
            if package.get("group_type") == "ACORD_BASED"
        )
        endorsement_only_count = sum(
            1 for package in submissions
            if package.get("group_type") == "ENDORSEMENT_ONLY"
        )
        print(f"\n    {company['company_name']}")
        print_value("Files", company.get("files_found", 0), indent=6)
        print_value("Submission groups", len(submissions), indent=6)
        print_value("ACORD-based", acord_count, indent=6)
        print_value("Endorsement-only", endorsement_only_count, indent=6)
        print_value("Unresolved", len(company.get("unassociated_records", [])), indent=6)
        print_value("File errors", len(company.get("file_errors", [])), indent=6)

if __name__ == "__main__":
    main()