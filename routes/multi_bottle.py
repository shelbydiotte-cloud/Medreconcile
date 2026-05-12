"""
routes/multi_bottle.py

POST /api/reconcile/multi
  Accepts multipart/form-data:
    image        — photo containing one or more medication bottles
    medications  — JSON array of {name, dose} EHR entries
    threshold    — int 50–100 (default 80)

Pipeline:
  1. OpenCV detects + crops each individual bottle
  2. Claude Vision extracts text from each crop
  3. Fuzzy matching runs per bottle
  4. Results merged into one report, keyed by bottle

GET /api/reconcile/multi/preview
  POST same multipart fields but returns an annotated JPEG
  showing the detected bounding boxes — useful for debugging.
"""

import json
import base64
from flask import Blueprint, request, jsonify, Response

from services.bottle_detector import detect_and_crop_bottles, annotate_detections
from services.reconciliation import extract_text_from_image, reconcile

multi_bp = Blueprint("multi_bottle", __name__)

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_BYTES = 20 * 1024 * 1024


def _validate_request():
    """Shared validation — returns (image_bytes, mime, medications, threshold) or raises."""
    if "image" not in request.files:
        return None, None, None, None, "No image file provided."

    f = request.files["image"]
    mime = f.mimetype or "image/jpeg"
    if mime not in ALLOWED_MIME:
        return None, None, None, None, f"Unsupported image type: {mime}"

    img_bytes = f.read()
    if len(img_bytes) == 0:
        return None, None, None, None, "Image file is empty."
    if len(img_bytes) > MAX_BYTES:
        return None, None, None, None, "Image exceeds 20 MB limit."

    raw_meds = request.form.get("medications", "[]")
    try:
        meds = json.loads(raw_meds)
        if not isinstance(meds, list) or not meds:
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        return None, None, None, None, "'medications' must be a non-empty JSON array."

    normalized = []
    for m in meds:
        if isinstance(m, str):
            normalized.append({"name": m, "dose": ""})
        elif isinstance(m, dict) and "name" in m:
            normalized.append({"name": m["name"], "dose": m.get("dose", "")})
        else:
            return None, None, None, None, f"Invalid medication entry: {m}"

    try:
        threshold = max(50, min(100, int(request.form.get("threshold", 80))))
    except ValueError:
        threshold = 80

    return img_bytes, mime, normalized, threshold, None


@multi_bp.route("/reconcile/multi", methods=["POST"])
def run_multi_bottle():
    """
    Full multi-bottle reconciliation pipeline.
    """
    img_bytes, mime, medications, threshold, err = _validate_request()
    if err:
        return jsonify({"error": err}), 400

    # ── Step 1: Detect bottles ────────────────────────────────────────────────
    try:
        bottles = detect_and_crop_bottles(img_bytes)
    except Exception as e:
        return jsonify({"error": f"Bottle detection failed: {e}"}), 500

    if not bottles:
        return jsonify({"error": "No bottles detected and fallback is disabled."}), 422

    # ── Step 2 + 3: OCR + reconcile each bottle ───────────────────────────────
    bottle_reports = []
    all_confirmed_meds = set()

    for bottle in bottles:
        idx = bottle["bottle_index"]
        crop_bytes = bottle["crop_bytes"]

        # OCR
        try:
            ocr_text = extract_text_from_image(crop_bytes, mime_type="image/jpeg")
        except Exception as e:
            ocr_text = ""
            ocr_error = str(e)
        else:
            ocr_error = None

        # Reconcile
        if ocr_text:
            report = reconcile(ocr_text, medications, threshold)
        else:
            report = {
                "confirmed": [], "needs_review": [], "discrepancies": medications[:],
                "summary": {"total": len(medications), "confirmed": 0, "needs_review": 0, "discrepancies": len(medications)},
            }

        # Track which meds were confirmed across any bottle
        for item in report["confirmed"]:
            all_confirmed_meds.add(item["med"])

        bottle_reports.append({
            "bottle_index": idx,
            "detection_method": bottle["detection"],
            "bounding_box": bottle["bounding_box"],
            "ocr_text": ocr_text,
            "ocr_error": ocr_error,
            "reconciliation": report,
        })

    # ── Step 4: Cross-bottle summary ─────────────────────────────────────────
    all_med_names = {m["name"] for m in medications}
    unmatched_across_all = sorted(all_med_names - all_confirmed_meds)

    response = {
        "bottles_detected": len(bottles),
        "threshold_used": threshold,
        "cross_bottle_summary": {
            "total_ehr_medications": len(medications),
            "confirmed_on_at_least_one_bottle": len(all_confirmed_meds),
            "not_found_on_any_bottle": unmatched_across_all,
        },
        "bottles": bottle_reports,
    }

    return jsonify(response), 200


@multi_bp.route("/reconcile/multi/preview", methods=["POST"])
def preview_detections():
    """
    Returns an annotated JPEG showing detected bounding boxes.
    Useful for verifying the OpenCV detection before running full OCR.
    """
    img_bytes, mime, _, _, err = _validate_request()
    if err:
        return jsonify({"error": err}), 400

    try:
        bottles = detect_and_crop_bottles(img_bytes)
        annotated = annotate_detections(img_bytes, bottles)
    except Exception as e:
        return jsonify({"error": f"Preview generation failed: {e}"}), 500

    return Response(
        annotated,
        mimetype="image/jpeg",
        headers={
            "X-Bottles-Detected": str(len(bottles)),
            "Content-Disposition": "inline; filename=detection_preview.jpg",
        },
    )
