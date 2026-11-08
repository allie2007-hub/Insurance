"""
Saves exactly what Azure Document Intelligence reads from one PDF,
so the ACORD 25 extraction can be fixed against real Azure output.

Usage (from the Insurance folder, with AZURE_DOC_INTEL_KEY set):
    python3 tools/dump_azure.py "Sample_Documents/Endorsements/Endorsements/Acuity/Acuity CB7242 (5-13).pdf"

Writes <file name>.azure.json next to this script. Send that file back.
It contains only the document's text and layout, never the key.
"""

import json
import os
import sys
import time
from pathlib import Path

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import DocumentAnalysisFeature
from azure.core.credentials import AzureKeyCredential


ENDPOINT = os.environ.get(
    "AZURE_DOC_INTEL_ENDPOINT",
    "https://ro-doc-intelligence.cognitiveservices.azure.com/",
)


def analyze(client, path, use_key_values):
    kwargs = {}

    if use_key_values:
        kwargs["features"] = [DocumentAnalysisFeature.KEY_VALUE_PAIRS]

    with open(path, "rb") as handle:
        poller = client.begin_analyze_document(
            "prebuilt-layout", body=handle, **kwargs
        )

    return poller.result()


def main():
    if len(sys.argv) != 2:
        sys.exit('Usage: python3 tools/dump_azure.py "path/to/file.pdf"')

    key = os.environ.get("AZURE_DOC_INTEL_KEY")

    if not key:
        sys.exit("Set AZURE_DOC_INTEL_KEY first (export AZURE_DOC_INTEL_KEY=...).")

    path = Path(sys.argv[1])
    client = DocumentIntelligenceClient(ENDPOINT, AzureKeyCredential(key))

    result = None
    used_key_values = True

    for attempt in range(3):
        try:
            result = analyze(client, path, used_key_values)
            break
        except Exception as error:  # noqa: BLE001
            message = str(error)
            print(f"Attempt {attempt + 1} failed: {message[:200]}")

            # Some pricing tiers do not offer key-value pairs; retry without.
            if "keyValuePairs" in message or "feature" in message.lower():
                used_key_values = False

            time.sleep(5)

    if result is None:
        sys.exit("Azure could not read the file after 3 attempts.")

    output = {
        "file": path.name,
        "key_value_pairs_requested": used_key_values,
        "pages": [
            {
                "page_number": page.page_number,
                "lines": [line.content for line in (page.lines or [])],
                "selection_marks": [
                    {
                        "state": mark.state,
                        "polygon": mark.polygon,
                    }
                    for mark in (page.selection_marks or [])
                ],
                "line_polygons": [
                    {"text": line.content, "polygon": line.polygon}
                    for line in (page.lines or [])
                ],
            }
            for page in result.pages
        ],
        "tables": [
            {
                "row_count": table.row_count,
                "column_count": table.column_count,
                "page": (
                    table.bounding_regions[0].page_number
                    if table.bounding_regions else None
                ),
                "cells": [
                    {
                        "row": cell.row_index,
                        "column": cell.column_index,
                        "kind": cell.kind,
                        "content": cell.content,
                    }
                    for cell in table.cells
                ],
            }
            for table in (result.tables or [])
        ],
        "key_value_pairs": [
            {
                "key": pair.key.content if pair.key else None,
                "value": pair.value.content if pair.value else None,
                "confidence": pair.confidence,
            }
            for pair in (result.key_value_pairs or [])
        ],
    }

    out_path = Path(__file__).resolve().parent / f"{path.stem}.azure.json"
    out_path.write_text(json.dumps(output, indent=1, default=str))

    print(f"Saved {out_path.name}: {len(output['pages'])} pages, "
          f"{len(output['tables'])} tables, "
          f"{len(output['key_value_pairs'])} key-value pairs")


if __name__ == "__main__":
    main()
