"""
R&O Additional Insured endorsement chart lookup.

R&O's "Additional Insured Chart (with missing Endorsements Highlighted).xlsx"
lists ~290 carrier and ISO endorsement forms and records, for each form,
whether it gives ongoing operations, completed operations, and
primary/non-contributory coverage, and whether it contains wording R&O
rejects or reviews (direct-contract wording, sole-negligence limits, etc.).

The chart is sponsor material, so it is NOT committed to GitHub. Put a copy
at data/reference/ai_chart.xlsx (ignored by git) or point the environment
variable RO_AI_CHART_PATH at it. If the chart is not found, lookups return
None and the pipeline falls back to keyword evidence only.
"""

import os
import re
from functools import lru_cache
from pathlib import Path


DEFAULT_CHART_PATH = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "reference"
    / "ai_chart.xlsx"
)


def normalize_form_number(value):
    """
    Reduces a form number to letters and digits so that
    "CG 20 10 07 04", "CG2010 0704" and "CG 20 10 (07/04)" compare equal.
    """

    if not value:
        return ""

    value = str(value).upper().replace("‐", "-").replace("‑", "-")

    return re.sub(r"[^A-Z0-9]", "", value)


def _marked(cell_value):
    """
    A chart cell counts as marked when it contains an X.
    Footnote markers (X*, X**, X¥, Xⱡ) mean "only when the contract
    requires it", which is still a mark.
    """

    return bool(cell_value) and "X" in str(cell_value).upper()


def _conditional(cell_value):
    """True when the mark carries a footnote (X*, X**, ...)."""

    if not _marked(cell_value):
        return False

    return str(cell_value).strip().upper() != "X"


@lru_cache(maxsize=1)
def load_chart(chart_path=None):
    """
    Loads the chart into {normalized_form_number: entry}.
    Returns an empty dict when the file or openpyxl is unavailable.
    """

    path = Path(
        chart_path
        or os.environ.get("RO_AI_CHART_PATH")
        or DEFAULT_CHART_PATH
    )

    if not path.exists():
        return {}

    try:
        import openpyxl
    except ImportError:
        return {}

    workbook = openpyxl.load_workbook(path, data_only=True)
    sheet = workbook.worksheets[0]

    entries = {}
    carrier = None

    # Row 1 is a group header, row 2 the column names, row 3 a disclaimer.
    for row in sheet.iter_rows(min_row=4, values_only=True):

        row = list(row) + [None] * 14

        if row[0]:
            carrier = str(row[0]).strip()

        form_number = row[1]

        if not form_number:
            continue

        form_number = (
            str(form_number)
            .replace("\xa0", " ")
            .replace("‐", "-")
            .replace("‑", "-")
            .strip()
        )
        key = normalize_form_number(form_number)

        if len(key) < 4:
            continue

        reject = (
            _marked(row[7])        # other coverage triggers
            or _marked(row[10])    # direct contract relationship wording
            or "REJECT" in str(row[13] or "").upper()
        )

        review = (
            _marked(row[9])        # vicarious / sole negligence limits
            or _marked(row[11])    # void if another AI form is provided
            or _marked(row[12])    # other exclusions / limitations
            or _marked(row[8])     # executed contract required
        )

        if reject:
            chart_decision = "REJECT"
        elif review:
            chart_decision = "REVIEW"
        else:
            chart_decision = "ACCEPT"

        entries[key] = {
            "carrier": carrier,
            "form_number": form_number,
            "ongoing_operations": _marked(row[2]),
            "completed_operations": _marked(row[3]),
            "completed_operations_conditional": _conditional(row[3]),
            "primary_noncontributory": _marked(row[4]),
            "primary_noncontributory_conditional": _conditional(row[4]),
            "trigger_arising_out_of": _marked(row[5]),
            "trigger_caused_in_whole_or_part": _marked(row[6]),
            "executed_contract_required": _marked(row[8]),
            "direct_contract_wording": _marked(row[10]),
            "chart_decision": chart_decision,
            "notes": (str(row[13]).strip() if row[13] else None),
        }

    return entries


def lookup_form(form_number, chart=None):
    """
    Returns the chart entry for a form number, or None.

    Matches exactly first; otherwise accepts a chart form that starts with
    the extracted number (an extracted number may lack the edition date).
    """

    chart = load_chart() if chart is None else chart

    key = normalize_form_number(form_number)

    if not key or not chart:
        return None

    if key in chart:
        return chart[key]

    candidates = [
        entry
        for chart_key, entry in chart.items()
        if len(key) >= 6
        and (chart_key.startswith(key) or key.startswith(chart_key))
    ]

    if len(candidates) == 1:
        return candidates[0]

    # Several editions of the same form: accept only when every edition
    # gives the same answer, otherwise the edition date matters.
    if candidates:
        fields = (
            "ongoing_operations",
            "completed_operations",
            "primary_noncontributory",
            "chart_decision",
        )
        signatures = {
            tuple(entry[field] for field in fields)
            for entry in candidates
        }

        if len(signatures) == 1:
            return {
                **candidates[0],
                "form_number": " / ".join(
                    entry["form_number"] for entry in candidates
                ),
                "edition_ambiguous": True,
            }

    return None
