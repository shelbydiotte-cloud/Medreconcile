"""
services/dose_extractor.py

Uses regex to find dose information near a medication name in OCR text,
then compares it against the EHR dose to generate priority alerts.

Supported dose formats (case-insensitive):
  10mg   10 mg   10MG   10.5mg
  10mcg  10 mcg  10ug
  10ml   10 ml
  10mEq  10 meq
  10unit(s)  10 IU  10 iu
  10%
  1/2 tablet  0.5 tab

Alert logic:
  HIGH     — numeric value differs        (10mg vs 20mg)
  MEDIUM   — unit differs                 (10mg vs 10mcg)
  LOW      — dose present in EHR but not found in scan (or vice versa)
  None     — both match, or one/both doses are unknown
"""

import re

# ── Regex patterns ─────────────────────────────────────────────────────────────

# Matches a dose value + unit within text, e.g. "10mg", "2.5 mcg", "500 MG"
DOSE_PATTERN = re.compile(
    r"""
    (?:^|[\s,(/-])                        # word boundary
    (\d+(?:\.\d+)?                        # integer or decimal  e.g. 10 or 0.5
      (?:\s*/\s*\d+(?:\.\d+)?)?          # optional fraction   e.g. 1/2
    )
    \s*                                   # optional space between number and unit
    (mg|mcg|ug|ml|meq|iu|units?|%)       # unit (case-insensitive)
    (?=[\s,.)/-]|$)                       # lookahead: word boundary
    """,
    re.IGNORECASE | re.VERBOSE,
)

# How many characters around a matched medication name to search for a dose
CONTEXT_WINDOW = 120


def _normalise_unit(unit: str) -> str:
    """Collapse synonyms to a canonical unit string."""
    u = unit.lower().strip()
    synonyms = {
        "ug": "mcg",
        "units": "unit",
        "units": "unit",
    }
    return synonyms.get(u, u)


def _parse_dose(dose_str: str) -> tuple[float | None, str]:
    """
    Parse a dose string like '10mg' or '2.5 mcg' into (value, unit).
    Returns (None, '') if unparseable.
    """
    if not dose_str:
        return None, ""
    m = DOSE_PATTERN.search(" " + dose_str.strip())
    if not m:
        return None, ""
    try:
        value = float(m.group(1).replace(" ", ""))
    except ValueError:
        return None, ""
    unit = _normalise_unit(m.group(2))
    return value, unit


def extract_dose_near_name(med_name: str, ocr_text: str) -> str:
    """
    Search OCR text for a dose value within CONTEXT_WINDOW characters of
    a fuzzy match for med_name.

    Returns the raw dose string (e.g. '20mg') or '' if nothing found.
    """
    # Find position of the medication name in the OCR text (case-insensitive)
    pattern = re.compile(re.escape(med_name), re.IGNORECASE)
    match = pattern.search(ocr_text)

    if not match:
        # Try each word of the name individually (handles partial OCR matches)
        words = [w for w in med_name.split() if len(w) > 3]
        for word in words:
            m = re.search(re.escape(word), ocr_text, re.IGNORECASE)
            if m:
                match = m
                break

    if not match:
        return ""

    # Extract a window of text around the match
    start = max(0, match.start() - 20)
    end = min(len(ocr_text), match.end() + CONTEXT_WINDOW)
    context = ocr_text[start:end]

    # Find the first dose in that window
    dose_match = DOSE_PATTERN.search(" " + context)
    if not dose_match:
        return ""

    raw = dose_match.group(1).strip() + dose_match.group(2).strip()
    return raw


def compare_doses(ehr_dose: str, scanned_dose: str) -> dict:
    """
    Compare the EHR dose against the scanned dose and return an alert dict.

    Returns:
      {
        "dose_alert": bool,
        "alert_priority": "high" | "medium" | "low" | None,
        "alert_message": str,
        "ehr_dose": str,
        "scanned_dose": str,
      }
    """
    result = {
        "dose_alert": False,
        "alert_priority": None,
        "alert_message": "",
        "ehr_dose": ehr_dose or "",
        "scanned_dose": scanned_dose or "",
    }

    if not ehr_dose and not scanned_dose:
        return result  # nothing to compare

    if ehr_dose and not scanned_dose:
        result.update({
            "dose_alert": True,
            "alert_priority": "low",
            "alert_message": f"EHR lists {ehr_dose} but no dose found on label.",
        })
        return result

    if not ehr_dose and scanned_dose:
        result.update({
            "dose_alert": True,
            "alert_priority": "low",
            "alert_message": f"Label shows {scanned_dose} but EHR has no dose recorded.",
        })
        return result

    ehr_val, ehr_unit = _parse_dose(ehr_dose)
    scan_val, scan_unit = _parse_dose(scanned_dose)

    if ehr_val is None or scan_val is None:
        # Can't parse one side — flag for manual check
        if ehr_dose.strip().lower() != scanned_dose.strip().lower():
            result.update({
                "dose_alert": True,
                "alert_priority": "medium",
                "alert_message": (
                    f"Dose mismatch (unparseable): EHR={ehr_dose} | Label={scanned_dose}"
                ),
            })
        return result

    # Both parsed successfully
    units_match = _normalise_unit(ehr_unit) == _normalise_unit(scan_unit)
    values_match = abs(ehr_val - scan_val) < 1e-6

    if not values_match:
        result.update({
            "dose_alert": True,
            "alert_priority": "high",
            "alert_message": (
                f"DOSE MISMATCH — EHR: {ehr_dose} | Label: {scanned_dose}"
            ),
        })
    elif not units_match:
        result.update({
            "dose_alert": True,
            "alert_priority": "medium",
            "alert_message": (
                f"Unit mismatch — EHR: {ehr_dose} | Label: {scanned_dose}"
            ),
        })
    # else: both match — no alert

    return result
