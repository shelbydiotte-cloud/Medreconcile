"""
routes/review.py

Implements the clinician review-and-confirm workflow and scan history.

POST /api/review/submit
  Called by the frontend after the multi-bottle scan completes.
  Saves the pending scan to SQLite and returns a scan_id for the review step.

GET  /api/review/<scan_id>
  Returns the pending scan record so the frontend can render the review UI.

POST /api/review/<scan_id>/confirm
  Clinician submits their final decisions (confirm / edit per medication).
  Marks the scan as confirmed/edited in the database.

GET  /api/history
  Returns paginated scan history summaries.
  Query params: mrn=<str>, limit=<int>, offset=<int>

GET  /api/history/<scan_id>
  Returns a single complete scan record with all medication rows.
"""

import json
from flask import Blueprint, request, jsonify
from services.scan_logger import (
    init_db, log_scan, confirm_scan, get_scan, get_history
)

review_bp = Blueprint("review", __name__)

# Ensure tables exist when this blueprint is loaded
init_db()


# ── Submit pending scan ───────────────────────────────────────────────────────

@review_bp.route("/review/submit", methods=["POST"])
def submit_for_review():
    """
    Save a completed multi-bottle scan result to the database.
    The frontend calls this immediately after receiving the reconciliation response.

    Body (JSON):
      patient_name   str
      patient_mrn    str
      care_setting   str
      bottles_count  int
      threshold      int
      medications    list[dict]   — flat list of all medication results across bottles
    """
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "JSON body required."}), 400

    required = ["patient_name", "patient_mrn", "medications"]
    for field in required:
        if field not in body:
            return jsonify({"error": f"Missing required field: '{field}'"}), 400

    meds = body["medications"]
    if not isinstance(meds, list) or not meds:
        return jsonify({"error": "'medications' must be a non-empty list."}), 400

    try:
        scan_id = log_scan(
            patient_name=body["patient_name"],
            patient_mrn=body["patient_mrn"],
            care_setting=body.get("care_setting", ""),
            bottles_count=int(body.get("bottles_count", 1)),
            threshold=int(body.get("threshold", 80)),
            medications=meds,
        )
    except Exception as e:
        return jsonify({"error": f"Failed to save scan: {e}"}), 500

    return jsonify({
        "scan_id": scan_id,
        "status": "pending_review",
        "message": "Scan saved. Awaiting clinician review.",
    }), 201


# ── Fetch pending scan for review ────────────────────────────────────────────

@review_bp.route("/review/<scan_id>", methods=["GET"])
def get_review(scan_id):
    """Return the full scan record for the review UI."""
    scan = get_scan(scan_id)
    if not scan:
        return jsonify({"error": f"Scan '{scan_id}' not found."}), 404
    return jsonify(scan), 200


# ── Clinician confirms / edits ────────────────────────────────────────────────

@review_bp.route("/review/<scan_id>/confirm", methods=["POST"])
def confirm_review(scan_id):
    """
    Finalise a scan after clinician review.

    Body (JSON):
      confirmed_by   str           — clinician name or ID
      edits          list[dict]    — optional per-medication overrides:
        [
          {
            "med_id":         int,    — scan_medications.id
            "finalized_name": str,    — override med name (optional)
            "finalized_dose": str,    — override dose (optional)
            "clinician_note": str     — free-text note (optional)
          }
        ]
    """
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "JSON body required."}), 400

    confirmed_by = body.get("confirmed_by", "").strip()
    if not confirmed_by:
        return jsonify({"error": "'confirmed_by' is required."}), 400

    edits = body.get("edits", [])
    if not isinstance(edits, list):
        return jsonify({"error": "'edits' must be a list."}), 400

    # Validate edit objects
    for edit in edits:
        if "med_id" not in edit:
            return jsonify({"error": "Each edit must include 'med_id'."}), 400

    success = confirm_scan(scan_id, confirmed_by, edits)
    if not success:
        return jsonify({"error": f"Scan '{scan_id}' not found."}), 404

    scan = get_scan(scan_id)
    return jsonify({
        "scan_id": scan_id,
        "status": scan["status"],
        "confirmed_by": confirmed_by,
        "confirmed_at": scan["confirmed_at"],
        "edits_applied": len(edits),
    }), 200


# ── History ───────────────────────────────────────────────────────────────────

@review_bp.route("/history", methods=["GET"])
def list_history():
    """
    Return scan history summaries, newest first.
    Query params:
      mrn    — filter by patient MRN
      limit  — default 50
      offset — default 0
    """
    mrn = request.args.get("mrn")
    try:
        limit = min(200, int(request.args.get("limit", 50)))
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        limit, offset = 50, 0

    records = get_history(patient_mrn=mrn, limit=limit, offset=offset)
    return jsonify({
        "total": len(records),
        "limit": limit,
        "offset": offset,
        "records": records,
    }), 200


@review_bp.route("/history/<scan_id>", methods=["GET"])
def get_history_detail(scan_id):
    """Return a single complete scan with all medication rows."""
    scan = get_scan(scan_id)
    if not scan:
        return jsonify({"error": f"Scan '{scan_id}' not found."}), 404
    return jsonify(scan), 200
