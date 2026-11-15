from pathlib import Path

from classifiers.document_classifier import (
    get_page_text,
    classify_page
)

from extractors.extraction_mapper import (
    build_document_schema
)


SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff"
}


def find_documents(input_folder):
    """
    Finds supported files recursively.

    This allows carrier folders to contain nested folders
    without requiring a known number of files.
    """

    input_folder = Path(input_folder)

    return sorted([
        file_path
        for file_path in input_folder.rglob("*")
        if (
            file_path.is_file()
            and file_path.suffix.lower()
            in SUPPORTED_EXTENSIONS
        )
    ])


def process_file(
    file_path,
    analyze_document_function
):
    """
    Processes one file.

    Multi-page endorsements are combined only when the classifier
    identifies a later page as ENDORSEMENT_CONTINUATION and the
    immediately preceding open document is an endorsement.
    """

    result = analyze_document_function(
        file_path
    )

    page_records = []

    # Tracks an endorsement that may continue onto
    # the following page.
    open_endorsement = None

    for page in result.pages:

        page_text = get_page_text(
            page
        )

        classification = classify_page(
            page_text
        )

        page_type = classification[
            "page_type"
        ]

        # -------------------------------------------------
        # Multi-page endorsement continuation
        # -------------------------------------------------

        if (
            page_type
            == "ENDORSEMENT_CONTINUATION"
            and open_endorsement is not None
        ):
            open_endorsement[
                "full_text"
            ] += "\n" + page_text

            open_endorsement[
                "page_numbers"
            ].append(
                page.page_number
            )

            merged_tags = sorted(
                set(
                    open_endorsement[
                        "evidence_tags"
                    ]
                )
                | set(
                    classification[
                        "evidence_tags"
                    ]
                )
            )

            open_endorsement[
                "evidence_tags"
            ] = merged_tags

            # Keep a record that this page was classified
            # as a continuation before being merged.
            open_endorsement.setdefault(
                "continuation_pages",
                []
            ).append({
                "page_number":
                    page.page_number,

                "page_type":
                    page_type,

                "classification_confidence":
                    classification.get(
                        "classification_confidence"
                    ),

                "classification_reasons":
                    classification.get(
                        "classification_reasons",
                        []
                    ),
            })

            # Rebuild the endorsement schema using all
            # merged page text and evidence.
            open_endorsement[
                "schema"
            ] = build_document_schema(
                "ENDORSEMENT",
                open_endorsement[
                    "full_text"
                ],
                merged_tags
            )

            continue

        # -------------------------------------------------
        # Normal standalone page
        # -------------------------------------------------

        schema = build_document_schema(
            page_type,
            page_text,
            classification[
                "evidence_tags"
            ],
            page=page
        )

        record = {
            "source_file":
                file_path.name,

            "source_path":
                str(file_path),

            "page_number":
                page.page_number,

            "page_numbers": [
                page.page_number
            ],

            "page_type":
                page_type,

            "classification_confidence":
                classification.get(
                    "classification_confidence"
                ),

            "classification_reasons":
                classification.get(
                    "classification_reasons",
                    []
                ),

            "revision_indicators":
                classification.get(
                    "revision_indicators",
                    []
                ),

            "evidence_tags":
                classification[
                    "evidence_tags"
                ],

            "schema":
                schema,

            "text_preview":
                page_text[:200],

            "full_text":
                page_text,

            "continuation_pages": [],
        }

        page_records.append(
            record
        )

        # -------------------------------------------------
        # Decide whether following page may continue
        # this endorsement
        # -------------------------------------------------

        if page_type == "ENDORSEMENT":
            open_endorsement = record

        else:
            open_endorsement = None

    return page_records


def process_intake_folder(
    input_folder,
    analyze_document_function
):
    """
    Processes every supported file in one folder,
    regardless of file count or number of pages.
    """

    files = find_documents(input_folder)

    all_page_records = []
    file_errors = []

    for file_path in files:

        print(
            f"\nAnalyzing file: "
            f"{file_path.name}"
        )

        try:
            page_records = process_file(
                file_path,
                analyze_document_function
            )

            all_page_records.extend(
                page_records
            )

        except Exception as error:

            file_errors.append({
                "source_file": file_path.name,
                "source_path": str(file_path),
                "error": str(error)
            })

            print(
                f"Could not process "
                f"{file_path.name}: {error}"
            )

    return {
        "input_folder": str(input_folder),
        "files_found": len(files),
        "page_records": all_page_records,
        "file_errors": file_errors
    }
