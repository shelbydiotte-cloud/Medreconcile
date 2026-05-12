"""
routes/ehr.py

GET  /api/ehr/patients          — list mock patients
GET  /api/ehr/patients/<mrn>    — get one patient + their medication list

In production: replace with real HL7 FHIR calls or your EHR database.
"""

from flask import Blueprint, jsonify
from mock_data.patients import PATIENTS

ehr_bp = Blueprint("ehr", __name__)


@ehr_bp.route("/ehr/patients", methods=["GET"])
def list_patients():
    summary = [
        {"mrn": p["mrn"], "name": p["name"], "dob": p["dob"]}
        for p in PATIENTS
    ]
    return jsonify(summary), 200


@ehr_bp.route("/ehr/patients/<mrn>", methods=["GET"])
def get_patient(mrn):
    patient = next((p for p in PATIENTS if p["mrn"] == mrn), None)
    if patient is None:
        return jsonify({"error": f"Patient MRN '{mrn}' not found."}), 404
    return jsonify(patient), 200
