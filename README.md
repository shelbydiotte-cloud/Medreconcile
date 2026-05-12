# MedReconcile — Backend

A Flask API server for medication reconciliation.  
Pairs with the `med_reconciliation.html` frontend.

---

## Architecture

```
medreconcile/
├── app.py                   # Flask entry point
├── requirements.txt
├── .env.example             # Copy → .env and add your API key
│
├── routes/
│   ├── reconcile.py         # POST /api/reconcile
│   └── ehr.py               # GET  /api/ehr/patients[/<mrn>]
│
├── services/
│   └── reconciliation.py    # OCR (Claude Vision) + fuzzy matching logic
│
├── mock_data/
│   └── patients.py          # Simulated EHR patient records
│
└── static/
    └── index.html           # Drop the frontend HTML here
```

---

## Setup

```bash
# 1. Clone / enter the project
cd medreconcile

# 2. Create a virtual environment
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Open .env and set ANTHROPIC_API_KEY=sk-ant-...

# 5. Place the frontend
cp path/to/med_reconciliation.html static/index.html

# 6. Run
python app.py
# → http://localhost:5000
```

---

## API Reference

### `POST /api/reconcile`

Runs the full pipeline: OCR → fuzzy match → report.

**Request** — `multipart/form-data`

| Field         | Type   | Required | Description                                      |
|---------------|--------|----------|--------------------------------------------------|
| `image`       | file   | Yes      | Prescription image (JPEG, PNG, WEBP — max 20 MB) |
| `medications` | string | Yes      | JSON array: `[{"name":"Lisinopril","dose":"10mg"},...]` |
| `threshold`   | int    | No       | Fuzzy match threshold 50–100 (default: 80)       |

**Response** — `application/json`

```json
{
  "ocr_text": "Patient prescribed Lisinopri 10mg and Metformin 500mg...",
  "threshold_used": 80,
  "summary": {
    "total": 5,
    "confirmed": 3,
    "needs_review": 1,
    "discrepancies": 1
  },
  "confirmed": [
    { "med": "Lisinopril", "dose": "10mg", "match": "Lisinopri", "confidence": 93 }
  ],
  "needs_review": [
    { "med": "Amlodipine", "dose": "5mg", "match": "Amlodipin", "confidence": 71, "reason": "Low-confidence match — verify manually" }
  ],
  "discrepancies": [
    { "med": "Atorvastatin", "dose": "20mg", "best_token": "prescribed", "best_score": 22, "reason": "Not detected on scanned label" }
  ]
}
```

---

### `GET /api/ehr/patients`

Returns a list of all mock patients.

```json
[
  { "mrn": "MRN-00472", "name": "Margaret O'Brien", "dob": "1948-03-12" }
]
```

---

### `GET /api/ehr/patients/<mrn>`

Returns a single patient record with full medication list.

```json
{
  "mrn": "MRN-00472",
  "name": "Margaret O'Brien",
  "dob": "1948-03-12",
  "care_setting": "Home health",
  "medications": [
    { "name": "Lisinopril", "dose": "10mg", "frequency": "Once daily" }
  ]
}
```

---

## Example curl

```bash
curl -X POST http://localhost:5000/api/reconcile \
  -F "image=@/path/to/label.jpg" \
  -F 'medications=[{"name":"Lisinopril","dose":"10mg"},{"name":"Metformin","dose":"500mg"}]' \
  -F "threshold=80"
```

---

## Production notes

- Move `ANTHROPIC_API_KEY` to a secrets manager (AWS Secrets Manager, GCP Secret Manager, etc.)
- Swap `mock_data/patients.py` for a real HL7 FHIR client or database queries
- Add authentication (JWT or OAuth2) to protect patient data
- Run behind `gunicorn` with an Nginx reverse proxy
- Enable HTTPS — never transmit patient images over plain HTTP
- Log reconciliation results to an audit trail (HIPAA requirement)
