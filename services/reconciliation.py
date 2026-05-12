"""
services/reconciliation.py

Handles:
  1. OCR extraction via Anthropic Vision API
  2. Fuzzy name matching via thefuzz
  3. Regex dose extraction + comparison (see dose_extractor.py)
"""

import base64
import anthropic
from thefuzz import process, fuzz
from services.dose_extractor import extract_dose_near_name, compare_doses


def extract_text_from_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    """Send image to Claude Vision and return extracted OCR text."""
    client = anthropic.Anthropic()
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "You are a medical OCR engine. Extract ALL text from this "
                            "prescription label or medication document exactly as it appears. "
                            "Return ONLY the raw extracted text — no explanations, no markdown, "
                            "no preamble. Preserve line breaks."
                        ),
                    },
                ],
            }
        ],
    )
    return message.content[0].text.strip()


def reconcile(
    scanned_text: str,
    ehr_medications: list[dict],
    threshold: int = 80,
) -> dict:
    """
    Compare each EHR medication against the scanned text.

    Steps per medication:
      1. Fuzzy name match (thefuzz WRatio) -> confirmed / needs_review / discrepancy
      2. Regex dose extraction near the matched name in OCR text
      3. Dose comparison against EHR dose -> priority alert if mismatch

    Each result item includes:
      med, ehr_dose, match, confidence, match_status,
      scanned_dose, dose_alert, alert_priority, alert_message
    """
    tokens = [
        w.strip(".,;:()")
        for w in scanned_text.split()
        if len(w.strip(".,;:()")) > 2
    ]

    confirmed = []
    needs_review = []
    discrepancies = []

    for med in ehr_medications:
        name = med["name"]
        ehr_dose = med.get("dose", "")

        # 1. Fuzzy name match
        result = process.extractOne(name, tokens, scorer=fuzz.WRatio)

        if result is None:
            discrepancies.append({
                "med": name,
                "ehr_dose": ehr_dose,
                "match_status": "discrepancy",
                "reason": "No tokens found in scanned text",
                "dose_alert": False,
                "alert_priority": None,
                "alert_message": "",
                "scanned_dose": "",
            })
            continue

        matched_token, score = result

        # 2. Regex dose extraction
        scanned_dose = extract_dose_near_name(name, scanned_text)

        # 3. Dose comparison
        dose_info = compare_doses(ehr_dose, scanned_dose)

        base_item = {
            "med": name,
            "ehr_dose": ehr_dose,
            "match": matched_token,
            "confidence": score,
            "scanned_dose": scanned_dose,
            **dose_info,
        }

        # 4. Bucket by fuzzy score
        if score >= threshold:
            confirmed.append({**base_item, "match_status": "confirmed"})
        elif score >= threshold - 15:
            needs_review.append({
                **base_item,
                "match_status": "needs_review",
                "reason": "Low-confidence name match — verify manually",
            })
        else:
            discrepancies.append({
                **base_item,
                "match_status": "discrepancy",
                "reason": "Medication name not detected on label",
            })

    # Escalate HIGH dose alerts from confirmed -> needs_review
    still_confirmed = []
    for item in confirmed:
        if item.get("alert_priority") == "high":
            item["reason"] = item["alert_message"]
            needs_review.insert(0, {**item, "match_status": "needs_review"})
        else:
            still_confirmed.append(item)

    return {
        "confirmed": still_confirmed,
        "needs_review": needs_review,
        "discrepancies": discrepancies,
        "summary": {
            "total": len(ehr_medications),
            "confirmed": len(still_confirmed),
            "needs_review": len(needs_review),
            "discrepancies": len(discrepancies),
            "dose_alerts": sum(
                1 for lst in (still_confirmed, needs_review, discrepancies)
                for i in lst if i.get("dose_alert")
            ),
            "high_priority_alerts": sum(
                1 for lst in (still_confirmed, needs_review, discrepancies)
                for i in lst if i.get("alert_priority") == "high"
            ),
        },
    }
