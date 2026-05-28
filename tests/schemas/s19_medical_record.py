from pydantic import BaseModel


class EmergencyContact(BaseModel):
    name: str
    relationship: str
    phone: str


class Patient(BaseModel):
    id: str
    name: str
    dob: str
    gender: str
    blood_type: str
    allergies: list[str]
    emergency_contact: EmergencyContact


class Vitals(BaseModel):
    bp_systolic: int
    bp_diastolic: int
    heart_rate: int
    temperature_f: float
    weight_kg: float


class Diagnosis(BaseModel):
    code: str
    description: str
    primary: bool


class Prescription(BaseModel):
    medication: str
    dosage: str
    frequency: str
    duration_days: int
    refills: int


class Visit(BaseModel):
    date: str
    provider: str
    department: str
    vitals: Vitals
    diagnoses: list[Diagnosis]
    prescriptions: list[Prescription]
    follow_up: bool
    notes: str


class MedicalRecord(BaseModel):
    patient: Patient
    visits: list[Visit]
