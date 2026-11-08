"""
Position-based reading of an ACORD 25 certificate.

Azure's line-by-line reading order does not follow the certificate's grid:
on real certificates a limit's "$" and amount can come before or after its
label, so reading the text in order finds the wrong amount or none at all.

Azure also returns where every line and checkbox sits on the page (inches
from the top-left corner). This module uses those positions the way a
person reads the form: each coverage row is a horizontal band, a limit is
the amount printed on the same height as its label, and a checkbox belongs
to the label printed just to its right.

Works with Azure SDK page objects or with plain dicts of the same shape
(as saved by dump_azure.py), so it can be tested without the Azure key.
"""

import re


# ---------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------

def _get(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)

    return getattr(obj, name, default)


class Box:
    """A text line or checkbox with its position on the page."""

    def __init__(self, text, polygon, state=None):
        xs = polygon[0::2]
        ys = polygon[1::2]

        self.text = (text or "").strip()
        self.x0, self.x1 = min(xs), max(xs)
        self.y0, self.y1 = min(ys), max(ys)
        self.cx = (self.x0 + self.x1) / 2
        self.cy = (self.y0 + self.y1) / 2
        self.state = state

    def __repr__(self):
        return f"Box({self.text!r}, x={self.x0:.2f}, y={self.cy:.2f})"


def page_boxes(page):
    """Returns (lines, checkboxes) for a page, or (None, None)."""

    lines = []

    for line in _get(page, "lines") or []:
        polygon = _get(line, "polygon")

        if polygon and len(polygon) >= 8:
            lines.append(Box(_get(line, "content"), list(polygon)))

    marks = []

    for mark in _get(page, "selection_marks") or []:
        polygon = _get(mark, "polygon")

        if polygon and len(polygon) >= 8:
            # The Azure SDK returns an enum (DocumentSelectionMarkState),
            # whose str() is "DocumentSelectionMarkState.SELECTED"; use its
            # value ("selected") so SDK objects and saved JSON agree.
            raw_state = _get(mark, "state") or ""
            state = str(getattr(raw_state, "value", raw_state)).lower()
            marks.append(Box("", list(polygon), state=state))

    if not lines:
        return None, None

    return lines, marks


def _norm(text):
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


MONEY = re.compile(r"\$?\s*(\d{1,3}(?:,\d{3})+|\d{4,})(?!\s*/)")
DATE = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$")


def _money(text):
    match = MONEY.search(text or "")

    if not match:
        return None

    return match.group(1).replace(",", "")


# ---------------------------------------------------------
# Coverage rows
# ---------------------------------------------------------

SECTION_LABELS = {
    "general_liability": ("COMMERCIALGENERALLIABILITY",),
    "automobile_liability": ("AUTOMOBILELIABILITY",),
    "umbrella_excess": ("UMBRELLALIAB", "EXCESSLIAB"),
    "workers_compensation": ("WORKERSCOMPENSATION",),
}

# Labels in the LIMITS column for each coverage (normalized text).
LIMIT_LABELS = {
    "general_liability": {
        "each_occurrence": "EACHOCCURRENCE",
        "general_aggregate": "GENERALAGGREGATE",
        "products_completed_operations": "PRODUCTSCOMPOPAGG",
        "personal_advertising_injury": "PERSONALADVINJURY",
    },
    "automobile_liability": {
        "combined_single_limit": "COMBINEDSINGLELIMIT",
    },
    "umbrella_excess": {
        "each_occurrence": "EACHOCCURRENCE",
        "aggregate": "AGGREGATE",
    },
    "workers_compensation": {
        "each_accident": "ELEACHACCIDENT",
        "disease_each_employee": "ELDISEASEEAEMPLOYEE",
        "disease_policy_limit": "ELDISEASEPOLICYLIMIT",
    },
}

# Column positions on the standard ACORD 25 (inches from the left).
LEFT_COLUMN_MAX_X = 2.4
ADDL_INSD_X = (2.40, 2.70)
SUBR_WVD_X = (2.70, 2.96)
POLICY_NUMBER_X = (2.90, 4.55)
EFFECTIVE_X = (4.45, 5.26)
EXPIRATION_X = (5.24, 5.95)
LIMIT_LABEL_X = (5.75, 7.15)
LIMIT_VALUE_MIN_X = 7.0

ROW_TOLERANCE = 0.13   # inches; limit label vs amount height difference
BAND_PADDING = 0.15    # labels can sit slightly above the coverage name


def find_sections(lines):
    """
    Returns {coverage: (top, bottom)} vertical bands, one per coverage row.
    """

    starts = []

    for line in lines:
        if line.x0 > LEFT_COLUMN_MAX_X:
            continue

        key = _norm(line.text)

        for coverage, labels in SECTION_LABELS.items():
            if any(key.startswith(label) for label in labels):
                starts.append((line.y0, coverage))
                break

    # Keep the first occurrence of each coverage (UMBRELLA and EXCESS
    # are two lines of the same row).
    first = {}

    for top, coverage in sorted(starts):
        first.setdefault(coverage, top)

    ordered = sorted((top, coverage) for coverage, top in first.items())

    description = [
        line.y0 for line in lines
        # The box heading, not the "describe under DESCRIPTION OF
        # OPERATIONS below" note inside the workers' comp row.
        if _norm(line.text).startswith("DESCRIPTIONOFOPERATIONSLOCATIONS")
    ]
    page_bottom = min(description) if description else 99.0

    # Other coverage rows (e.g. "Install Floater", "Pollution Liability")
    # start with an insurer letter in the INSR LTR column.
    letter_rows = sorted(
        line.y0 for line in lines
        if re.fullmatch(r"[A-F]", line.text) and line.x0 < 0.5
    )

    bands = {}

    for index, (top, coverage) in enumerate(ordered):
        if index + 1 < len(ordered):
            bottom = ordered[index + 1][0] - BAND_PADDING
        elif coverage == "workers_compensation":
            # The three E.L. limits run down to the next row's top edge;
            # only E.L. labels are read in this band, so extending it to
            # the description box cannot pick up another row's limits.
            bottom = page_bottom
        else:
            later_rows = [y for y in letter_rows if y > top + 0.5]
            bottom = min([page_bottom] + later_rows) - 0.02

        bands[coverage] = (top - BAND_PADDING, bottom)

    return bands


def _in_band(box, band):
    return band[0] <= box.cy < band[1]


def _limit_value(label, lines):
    """The amount printed in the LIMITS column at the label's height."""

    inline = _money(label.text)

    if inline:
        return inline

    best = None

    for line in lines:
        if line.x0 < LIMIT_VALUE_MIN_X or line is label:
            continue

        amount = _money(line.text)

        if not amount:
            continue

        distance = abs(line.cy - label.cy)

        if distance <= ROW_TOLERANCE and (
            best is None or distance < best[0]
        ):
            best = (distance, amount)

    return best[1] if best else None


def _limits_for(coverage, band, lines):
    found = {}
    labels = LIMIT_LABELS[coverage]

    for line in lines:
        if not _in_band(line, band):
            continue

        if not (LIMIT_LABEL_X[0] <= line.x0 <= LIMIT_LABEL_X[1]):
            continue

        key = _norm(line.text)

        for field, label in labels.items():
            if field in found:
                continue

            # "AGGREGATE" must not match "GENERAL AGGREGATE".
            if key == label or (
                key.startswith(label) and field != "aggregate"
            ):
                found[field] = _limit_value(line, lines)

    return found


def _row_fields(band, lines):
    """Policy number, dates and ADDL/SUBR flags on the coverage row."""

    top_band = (band[0], min(band[1], band[0] + 0.45))
    row = [line for line in lines if _in_band(line, top_band)]

    fields = {
        "policy_number": None,
        "effective_date": None,
        "expiration_date": None,
        "addl_insd": False,
        "subr_wvd": False,
    }

    for line in sorted(row, key=lambda box: box.cy):
        text = line.text

        if DATE.match(text):
            if EFFECTIVE_X[0] <= line.x0 < EFFECTIVE_X[1]:
                fields["effective_date"] = fields["effective_date"] or text
            elif EXPIRATION_X[0] <= line.x0 < EXPIRATION_X[1]:
                fields["expiration_date"] = fields["expiration_date"] or text
            continue

        if text.upper() in {"X", "Y"}:
            if ADDL_INSD_X[0] <= line.cx < ADDL_INSD_X[1]:
                fields["addl_insd"] = True
            elif SUBR_WVD_X[0] <= line.cx < SUBR_WVD_X[1]:
                fields["subr_wvd"] = True
            continue

        if (
            fields["policy_number"] is None
            and POLICY_NUMBER_X[0] <= line.x0 < POLICY_NUMBER_X[1]
            and re.search(r"\d", text)
            and re.fullmatch(r"[A-Z0-9][A-Z0-9 \-/.]{3,}", text.upper())
        ):
            fields["policy_number"] = text

    return fields


# ---------------------------------------------------------
# Checkboxes
# ---------------------------------------------------------

def _checked(label, marks, x_marks):
    """
    True if the checkbox just left of the label is ticked.
    Azure reports ticks either as a selection mark or as an "X" line.
    """

    for mark in marks + x_marks:
        overlaps = mark.y0 < label.y1 and mark.y1 > label.y0
        gap = label.x0 - mark.x1

        if overlaps and -0.05 <= gap <= 0.35:
            if mark.state is None or mark.state == "selected":
                return True

    return False


def _find_label(lines, band, *keys):
    for line in lines:
        if _in_band(line, band) and _norm(line.text) in keys:
            return line

    return None


def _checkbox_fields(coverage, band, lines, marks, x_marks):
    def ticked(*keys):
        label = _find_label(lines, band, *keys)
        return _checked(label, marks, x_marks) if label else None

    if coverage == "general_liability":
        occur = ticked("OCCUR")
        claims = ticked("CLAIMSMADE")
        project = ticked("PRO", "PROJECT")
        policy = ticked("POLICY")
        location = ticked("LOC")

        return {
            "occurrence_form": (
                "OCCURRENCE" if occur and not claims
                else "CLAIMS_MADE" if claims and not occur
                else None
            ),
            "aggregate_applies_per": (
                "PROJECT" if project
                else "LOCATION" if location
                else "POLICY" if policy
                else None
            ),
        }

    if coverage == "automobile_liability":
        return {
            "any_auto": ticked("ANYAUTO"),
            "owned_autos": ticked("OWNED", "ALLOWNED", "ALLOWNEDAUTOS"),
            "hired_autos": ticked("HIRED", "HIREDAUTOS"),
            "non_owned_autos": ticked("NONOWNED", "NONOWNEDAUTOS"),
        }

    if coverage == "umbrella_excess":
        occur = ticked("OCCUR")
        claims = ticked("CLAIMSMADE")

        return {
            "occurrence_form": (
                "OCCURRENCE" if occur and not claims
                else "CLAIMS_MADE" if claims and not occur
                else None
            ),
        }

    if coverage == "workers_compensation":
        return {"per_statute": ticked("PER", "PERSTATUTE")}

    return {}


# ---------------------------------------------------------
# Header blocks
# ---------------------------------------------------------

def extract_certificate_holder_layout(lines):
    """
    The holder's name and address sit in the bottom-left box under the
    CERTIFICATE HOLDER heading; the cancellation notice is on the right.
    """

    heading = next(
        (line for line in lines if _norm(line.text) == "CERTIFICATEHOLDER"),
        None
    )

    if heading is None:
        return None

    values = []

    for line in sorted(lines, key=lambda box: (box.y0, box.x0)):
        if not (heading.y1 - 0.02 < line.cy < heading.y1 + 1.1):
            continue

        if line.x1 > 4.3:
            continue

        text = line.text

        # Holder codes such as "KIERCON" are the broker's lookup key.
        if re.fullmatch(r"[A-Z0-9\-]{3,12}", text) and not re.search(
            r"\d{5}", text
        ):
            continue

        if _norm(text).startswith(("ACORD", "AUTHORIZEDREPRESENTATIVE")):
            continue

        values.append(text)

    return "\n".join(values) or None


def extract_insurers_layout(lines):
    """INSURER A-F names, whether on the label's line or beside it."""

    insurers = {}

    for line in lines:
        match = re.match(r"INSURER\s+([A-F])\b\s*:?\s*(.*)", line.text, re.I)

        if not match:
            continue

        letter = match.group(1).lower()
        name = match.group(2).strip()

        if not name:
            beside = [
                other for other in lines
                if other is not line
                and abs(other.cy - line.cy) < 0.09
                and line.x1 - 0.05 <= other.x0 < 7.3
                and not re.fullmatch(r"\d+", other.text)
            ]

            if beside:
                name = min(beside, key=lambda box: box.x0).text

        if name:
            insurers[f"insurer_{letter}"] = name

    return insurers


# ---------------------------------------------------------
# Entry point
# ---------------------------------------------------------

def extract_acord_layout(page):
    """
    Returns {"coverages": {...}, "certificate_holder": str|None,
    "insurers": {...}} from positions, or None when the page has no
    position data (then the text-based extractor is used instead).
    """

    lines, marks = page_boxes(page)

    if not lines:
        return None

    x_marks = [
        Box("X", [line.x0, line.y0, line.x1, line.y0,
                  line.x1, line.y1, line.x0, line.y1], state=None)
        for line in lines if line.text.upper() == "X"
    ]

    bands = find_sections(lines)
    coverages = {}

    for coverage in SECTION_LABELS:
        band = bands.get(coverage)

        if band is None:
            continue

        fields = _row_fields(band, lines)
        fields.update(_limits_for(coverage, band, lines))
        fields.update(_checkbox_fields(coverage, band, lines, marks, x_marks))
        coverages[coverage] = fields

    return {
        "coverages": coverages,
        "certificate_holder": extract_certificate_holder_layout(lines),
        "insurers": extract_insurers_layout(lines),
    }
