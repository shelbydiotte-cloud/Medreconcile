"""
routes/reconcile.py

POST /api/reconcile
  - Accepts multipart/form-data:
      image        : prescription image file
      medications  : JSON array of {name, dose} objects
      threshold    : int 50–100 (default 80)

  - Returns JSON reconciliation report
"""

import json
from flask import Blueprint, request, jsonify
from services.reconciliation import extract_text_from_image, reconcile

reconcile_bp = Blueprint("reconcile", __name__)

ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB


@reconcile_bp.route("/reconcile", methods=["POST"])
def run_reconcile():
    # --- Validate image ---
    if "image" not in request.files:
        return jsonify({"error": "No image file provided. Send as multipart field 'image'."}), 400

    img_file = request.files["image"]
    mime_type = img_file.mimetype or "image/jpeg"

    if mime_type not in ALLOWED_MIME_TYPES:
        return jsonify({"error": f"Unsupported image type: {mime_type}"}), 415

    image_bytes = img_file.read()
    if len(image_bytes) > MAX_IMAGE_BYTES:
        return jsonify({"error": "Image exceeds 20 MB limit."}), 413
    if len(image_bytes) == 0:
        return jsonify({"error": "Image file is empty."}), 400

    # --- Validate medications ---
    raw_meds = request.form.get("medications", "[]")
    try:
        medications = json.loads(raw_meds)
        if not isinstance(medications, list) or len(medications) == 0:
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        return jsonify({"error": "'medications' must be a non-empty JSON array."}), 400

    # Normalise medication objects
    normalized = []
    for m in medications:
        if isinstance(m, str):
            normalized.append({"name": m, "dose": ""})
        elif isinstance(m, dict) and "name" in m:
            normalized.append({"name": m["name"], "dose": m.get("dose", "")})
        else:
            return jsonify({"error": f"Invalid medication entry: {m}"}), 400

    # --- Threshold ---
    try:
        threshold = int(request.form.get("threshold", 80))
        threshold = max(50, min(100, threshold))
    except ValueError:
        threshold = 80

    # --- Run pipeline ---
    try:
        ocr_text = extract_text_from_image(image_bytes, mime_type)
    except Exception as e:
        return jsonify({"error": f"OCR extraction failed: {str(e)}"}), 502

    if not ocr_text:
        return jsonify({"error": "No text could be extracted from the image."}), 422

    report = reconcile(ocr_text, normalized, threshold)
    report["ocr_text"] = ocr_text
    report["threshold_used"] = threshold

    return jsonify(report), 200
