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
    Processes one file and returns one record per page.
    """

    result = analyze_document_function(file_path)

    page_records = []

    # Multi-page endorsements: only page 1 carries the
    # "THIS ENDORSEMENT CHANGES THE POLICY" header, so later pages were
    # classified OTHER and their wording (completed operations, primary
    # and non-contributory, exclusions) was lost. Unclassified pages that
    # follow an endorsement in the same file are now joined to it.
    open_endorsement = None

    for page in result.pages:

        page_text = get_page_text(page)

        classification = classify_page(
            page_text
        )

        if (
            classification["page_type"] == "OTHER"
            and open_endorsement is not None
        ):
            open_endorsement["full_text"] += "\n" + page_text
            open_endorsement["page_numbers"].append(
                page.page_number
            )

            merged_tags = sorted(
                set(open_endorsement["evidence_tags"])
                | set(classification["evidence_tags"])
            )
            open_endorsement["evidence_tags"] = merged_tags

            open_endorsement["schema"] = build_document_schema(
                "ENDORSEMENT",
                open_endorsement["full_text"],
                merged_tags
            )

            continue

        schema = build_document_schema(
            classification["page_type"],
            page_text,
            classification["evidence_tags"],
            page=page
        )

        record = {
            "source_file": file_path.name,
            "source_path": str(file_path),
            "page_number": page.page_number,
            "page_numbers": [page.page_number],
            "page_type":
                classification["page_type"],
            "evidence_tags":
                classification["evidence_tags"],
            "schema": schema,
            "text_preview": page_text[:200],
            "full_text": page_text,
        }

        page_records.append(record)

        open_endorsement = (
            record
            if classification["page_type"] == "ENDORSEMENT"
            else None
        )

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
