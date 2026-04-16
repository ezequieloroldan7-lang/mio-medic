from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from models import EstadoTurno

# ── Paciente ────────────────────────────────────────────────
class PacienteBase(BaseModel):
    nombre: str; apellido: str
    telefono: Optional[str]=None; email: Optional[str]=None
    dni: Optional[str]=None; nro_hc: Optional[str]=None
    financiador: Optional[str]=None; plan: Optional[str]=None; deriva: Optional[str]=None

class PacienteCreate(PacienteBase): pass
class PacienteOut(PacienteBase):
    id: int
    class Config: from_attributes = True

# ── Especialidad ─────────────────────────────────────────────
class EspecialidadOut(BaseModel):
    id: int; nombre: str
    class Config: from_attributes = True

# ── Horario ──────────────────────────────────────────────────
class HorarioBase(BaseModel):
    dia_semana: int; hora_inicio: str; hora_fin: str; consultorio: int = 1

class HorarioCreate(HorarioBase): pass
class HorarioOut(HorarioBase):
    id: int; medico_id: int
    class Config: from_attributes = True

# ── Medico ────────────────────────────────────────────────────
class MedicoBase(BaseModel):
    nombre: str; apellido: str; especialidad_id: int
    telefono: Optional[str]=None; email: Optional[str]=None
    matricula: Optional[str]=None
    google_calendar_id: Optional[str]=None

class MedicoCreate(MedicoBase): pass
class MedicoOut(MedicoBase):
    id: int
    especialidad: Optional[EspecialidadOut] = None
    horarios: List[HorarioOut] = []
    class Config: from_attributes = True

# ── Turno ─────────────────────────────────────────────────────
class TurnoBase(BaseModel):
    paciente_id: int; medico_id: int; consultorio: int
    fecha_hora_inicio: datetime; duracion_minutos: int = 45
    observaciones: Optional[str]=None

class TurnoCreate(TurnoBase): pass
class TurnoUpdate(BaseModel):
    paciente_id:       Optional[int]         = None
    medico_id:         Optional[int]         = None
    consultorio:       Optional[int]         = None
    fecha_hora_inicio: Optional[datetime]    = None
    duracion_minutos:  Optional[int]         = None
    estado:            Optional[EstadoTurno] = None
    observaciones:     Optional[str]         = None

class TurnoOut(TurnoBase):
    id: int; estado: EstadoTurno; whatsapp_enviado: bool
    google_event_id: Optional[str]=None
    paciente: Optional[PacienteOut]=None; medico: Optional[MedicoOut]=None
    class Config: from_attributes = True

# ── Auth / User ──────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str; password: str

class TokenOut(BaseModel):
    access_token: str; token_type: str = "bearer"
    user: "UserOut"

class UserOut(BaseModel):
    id: int; username: str; display_name: str
    role: str; medico_id: Optional[int] = None
    class Config: from_attributes = True

class UserCreate(BaseModel):
    username: str; password: str; display_name: str
    role: str = "medico"; medico_id: Optional[int] = None

class ChangePassword(BaseModel):
    current_password: str; new_password: str
