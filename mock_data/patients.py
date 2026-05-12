"""
mock_data/patients.py

Simulated EHR patient records.
Replace with a real database or HL7 FHIR integration in production.
"""

PATIENTS = [
    {
        "mrn": "MRN-00472",
        "name": "Margaret O'Brien",
        "dob": "1948-03-12",
        "care_setting": "Home health",
        "medications": [
            {"name": "Lisinopril",    "dose": "10mg",  "frequency": "Once daily"},
            {"name": "Metformin",     "dose": "500mg", "frequency": "Twice daily"},
            {"name": "Atorvastatin",  "dose": "20mg",  "frequency": "Once daily"},
            {"name": "Amlodipine",    "dose": "5mg",   "frequency": "Once daily"},
            {"name": "Omeprazole",    "dose": "20mg",  "frequency": "Once daily"},
        ],
    },
    {
        "mrn": "MRN-00891",
        "name": "Robert Chen",
        "dob": "1955-07-29",
        "care_setting": "Outpatient",
        "medications": [
            {"name": "Warfarin",      "dose": "5mg",   "frequency": "Once daily"},
            {"name": "Carvedilol",    "dose": "12.5mg","frequency": "Twice daily"},
            {"name": "Furosemide",    "dose": "40mg",  "frequency": "Once daily"},
            {"name": "Spironolactone","dose": "25mg",  "frequency": "Once daily"},
        ],
    },
    {
        "mrn": "MRN-01103",
        "name": "Diane Patel",
        "dob": "1963-11-04",
        "care_setting": "Long-term care",
        "medications": [
            {"name": "Levothyroxine", "dose": "75mcg", "frequency": "Once daily"},
            {"name": "Sertraline",    "dose": "50mg",  "frequency": "Once daily"},
            {"name": "Gabapentin",    "dose": "300mg", "frequency": "Three times daily"},
            {"name": "Metoprolol",    "dose": "25mg",  "frequency": "Twice daily"},
            {"name": "Aspirin",       "dose": "81mg",  "frequency": "Once daily"},
        ],
    },
]
