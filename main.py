import os
import time
from pathlib import Path

from azure.ai.documentintelligence import (
    DocumentIntelligenceClient
)
from azure.core.credentials import (
    AzureKeyCredential
)

from classifiers.document_classifier import (
    get_page_text,
    classify_page
)

from extractors.extraction_mapper import (
    build_document_schema
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

# from Submission.submission_package import (
#     build_submission_package
# )



# Azure Document Intelligence Configuration
# Set these in your shell or a .env file (never commit the key):
#   export AZURE_DOC_INTEL_ENDPOINT="https://ro-doc-intelligence.cognitiveservices.azure.com/"
#   export AZURE_DOC_INTEL_KEY="..."
endpoint = os.environ.get(
    "AZURE_DOC_INTEL_ENDPOINT",
    "https://ro-doc-intelligence.cognitiveservices.azure.com/"
)
key = os.environ.get("AZURE_DOC_INTEL_KEY")

if not key:
    raise SystemExit(
        "Set the AZURE_DOC_INTEL_KEY environment variable first."
    )

client = DocumentIntelligenceClient(
    endpoint=endpoint,
    credential=AzureKeyCredential(key)
)


def analyze_document(file_path):
    """
    Sends the PDF to Azure Document Intelligence
    and returns the analysis result.
    """

    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(
            f"Document was not found: {file_path.resolve()}"
        )

    # Connection resets and rate limits (HTTP 429) are temporary, so each
    # file gets three tries before it is reported as a file error.
    # A wrong key (HTTP 401) is not retried.
    for attempt in range(1, 4):
        try:
            with file_path.open("rb") as document_file:
                poller = client.begin_analyze_document(
                    "prebuilt-layout",
                    body=document_file
                )
                return poller.result()

        except Exception as error:  # noqa: BLE001
            message = str(error)

            # Permanent errors are not retried: a wrong key (401) or a
            # request Azure refuses (400 InvalidRequest), which on the free
            # F0 tier usually means the PDF is larger than 4 MB.
            if attempt == 3 or "401" in message or "InvalidRequest" in message:
                if "InvalidRequest" in message:
                    size_mb = file_path.stat().st_size / 1_000_000
                    raise RuntimeError(
                        f"Azure rejected the file ({size_mb:.1f} MB). "
                        "The free Azure tier only accepts files up to 4 MB."
                    ) from error
                raise

            print(
                f"  Retrying {file_path.name} "
                f"(attempt {attempt + 1} of 3): {message[:80]}"
            )
            time.sleep(5 * attempt)


def main():

    documents_root = (
        Path(__file__).parent
        / "Sample_Documents"
        / "Endorsements"
        / "Endorsements"
    )

    if not documents_root.exists():
        raise FileNotFoundError(
            f"Sample documents folder not found: "
            f"{documents_root.resolve()}"
        )

    company_folders = sorted([
        folder
        for folder in documents_root.iterdir()
        if folder.is_dir()
    ])

    print(
        f"\nCompanies found: "
        f"{len(company_folders)}"
    )

    all_company_results = []

    for company_folder in company_folders:

        company_name = company_folder.name

        print("\n" + "=" * 70)
        print(f"PROCESSING COMPANY: {company_name}")
        print("=" * 70)

        intake_result = process_intake_folder(
            company_folder,
            analyze_document
        )

        page_records = intake_result[
            "page_records"
        ]

        print("\nINTAKE SUMMARY")
        print(
            "Company:",
            company_name
        )
        print(
            "Files found:",
            intake_result["files_found"]
        )
        print(
            "Pages processed:",
            len(page_records)
        )
        print(
            "File errors:",
            len(
                intake_result["file_errors"]
            )
        )

        # Do not run association if the folder is empty.
        if not page_records:

            all_company_results.append({
                "company_name": company_name,
                "status": "NO_SUPPORTED_FILES",
                "files_found":
                    intake_result["files_found"],
                "file_errors":
                    intake_result["file_errors"],
                "submissions": []
            })

            continue

        association_batch = associate_batch(
            page_records
        )

        print("\nASSOCIATION SUMMARY")
        print(
            "ACORD 25 pages:",
            association_batch["acord_count"]
        )
        print(
            "Endorsement pages:",
            association_batch[
                "endorsement_count"
            ]
        )
        print(
            "Candidate submissions:",
            len(
                association_batch[
                    "submissions"
                ]
            )
        )
        print(
            "Unassociated pages:",
            len(
                association_batch[
                    "unassociated_records"
                ]
            )
        )

        company_packages = []

        for submission in association_batch[
            "submissions"
        ]:

            acord_record = submission[
                "acord_record"
            ]

            endorsement_records = submission[
                "endorsement_records"
            ]

            acord_schema = acord_record[
                "schema"
            ]

            endorsement_schemas = [
                record["schema"]
                for record
                in endorsement_records
            ]

            compliance_results = (
                run_compliance_checks(
                    acord_schema,
                    endorsement_schemas
                )
            )

            # Make the submission ID unique across companies.
            submission_id = (
                f"{company_name}-"
                f"{submission['submission_id']}"
            )

            final_package = {
                "company_name":
                    company_name,

                "company_folder":
                    str(company_folder),

                "submission_id":
                    submission_id,

                "overall_status":
                    compliance_results[
                        "overall_status"
                    ],

                "source_acord": {
                    "file":
                        acord_record[
                            "source_file"
                        ],

                    "page":
                        acord_record[
                            "page_number"
                        ]
                },

                "associated_endorsements": [
                    {
                        "file":
                            record[
                                "source_file"
                            ],

                        "page":
                            record[
                                "page_number"
                            ],

                        "evidence_tags":
                            record[
                                "evidence_tags"
                            ]
                    }

                    for record
                    in endorsement_records
                ],

                "named_insured":
                    acord_schema.get(
                        "named_insured"
                    ),

                "policy_numbers": {
                    coverage_name:
                        coverage_data.get(
                            "policy_number"
                        )

                    for (
                        coverage_name,
                        coverage_data
                    )

                    in acord_schema.get(
                        "coverages",
                        {}
                    ).items()
                },

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
                    )
            }

            company_packages.append(
                final_package
            )

        company_result = {
            "company_name":
                company_name,

            "status":
                "PROCESSED",

            "files_found":
                intake_result[
                    "files_found"
                ],

            "pages_processed":
                len(page_records),

            "file_errors":
                intake_result[
                    "file_errors"
                ],

            "submissions":
                company_packages,

            "unassociated_records":
                association_batch[
                    "unassociated_records"
                ],

            "other_records":
                association_batch[
                    "other_records"
                ]
        }

        all_company_results.append(
            company_result
        )

        print("\nFINAL SUBMISSION PACKAGES")

        if not company_packages:
            print(
                "No complete submission package "
                "could be created."
            )

        for package in company_packages:

            print(
                f"\nCompany: "
                f"{package['company_name']}"
            )

            print(
                "Submission:",
                package["submission_id"]
            )

            print(
                "Overall status:",
                package["overall_status"]
            )

            print(
                "ACORD source:",
                package["source_acord"]
            )

            print(
                "Associated endorsements:",
                package[
                    "associated_endorsements"
                ]
            )

            print(
                "Evidence:",
                package["evidence"]
            )

            print(
                "Checks:",
                package["checks"]
            )

        if association_batch[
            "unassociated_records"
        ]:

            print(
                "\nHUMAN REVIEW - "
                "UNASSOCIATED DOCUMENTS"
            )

            for item in association_batch[
                "unassociated_records"
            ]:

                record = item["record"]

                print({
                    "company_name":
                        company_name,

                    "file":
                        record[
                            "source_file"
                        ],

                    "page":
                        record[
                            "page_number"
                        ],

                    "reason":
                        item["reason"]
                })

    # Final summary across companies.
    print("\n" + "=" * 70)
    print("ALL-COMPANY PROCESSING SUMMARY")
    print("=" * 70)

    total_submissions = sum(
        len(company["submissions"])
        for company in all_company_results
    )

    print(
        "Companies processed:",
        len(all_company_results)
    )

    print(
        "Candidate submissions created:",
        total_submissions
    )

    for company in all_company_results:

        print({
            "company":
                company["company_name"],

            "status":
                company["status"],

            "files_found":
                company["files_found"],

            "submissions_created":
                len(
                    company["submissions"]
                ),

            "file_errors":
                len(
                    company["file_errors"]
                )
        })


if __name__ == "__main__":
    main()