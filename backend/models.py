from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship
from database import Base
from crypto import EncryptedStr
import enum
from datetime import datetime


class EstadoTurno(str, enum.Enum):
    pendiente  = "pendiente"
    confirmado = "confirmado"
    cancelado  = "cancelado"
    ausente    = "ausente"
    realizado  = "realizado"


class Paciente(Base):
    __tablename__ = "pacientes"
    id        = Column(Integer, primary_key=True, index=True)
    nombre    = Column(String, nullable=False, index=True)
    apellido  = Column(String, nullable=False, index=True)
    # PII cifrado at-rest (AES-GCM con FIELD_ENCRYPTION_KEY). Transparente al caller.
    telefono  = Column(EncryptedStr)
    email     = Column(EncryptedStr)
    dni       = Column(String, index=True)   # sigue en claro para permitir LIKE search
    nro_hc    = Column(String, index=True)
    financiador = Column(String, index=True)
    plan        = Column(String)
    deriva    = Column(String)
    turnos    = relationship("Turno", back_populates="paciente")


class Especialidad(Base):
    __tablename__ = "especialidades"
    id      = Column(Integer, primary_key=True, index=True)
    nombre  = Column(String, unique=True, nullable=False)
    medicos = relationship("Medico", back_populates="especialidad")


class Medico(Base):
    __tablename__ = "medicos"
    id              = Column(Integer, primary_key=True, index=True)
    nombre          = Column(String, nullable=False)
    apellido        = Column(String, nullable=False, index=True)
    especialidad_id = Column(Integer, ForeignKey("especialidades.id"), index=True)
    # PII del profesional cifrado at-rest
    telefono        = Column(EncryptedStr)
    email           = Column(EncryptedStr)
    matricula           = Column(String)
    google_calendar_id  = Column(String)   # email del Google Calendar (ej. dr@gmail.com)
    ical_token          = Column(String)   # token para feed público .ics (URL firmada)
    especialidad    = relationship("Especialidad", back_populates="medicos")
    turnos          = relationship("Turno", back_populates="medico")
    horarios        = relationship("HorarioMedico", back_populates="medico", cascade="all, delete-orphan")


class HorarioMedico(Base):
    __tablename__ = "horarios_medico"
    id           = Column(Integer, primary_key=True, index=True)
    medico_id    = Column(Integer, ForeignKey("medicos.id"), nullable=False, index=True)
    dia_semana   = Column(Integer, nullable=False)   # 0=Lun … 4=Vie
    hora_inicio  = Column(String, nullable=False)    # "09:00"
    hora_fin     = Column(String, nullable=False)    # "13:00"
    consultorio  = Column(Integer, default=1)
    medico       = relationship("Medico", back_populates="horarios")


class Turno(Base):
    __tablename__ = "turnos"
    id                = Column(Integer, primary_key=True, index=True)
    paciente_id       = Column(Integer, ForeignKey("pacientes.id"), nullable=False, index=True)
    medico_id         = Column(Integer, ForeignKey("medicos.id"), nullable=False, index=True)
    consultorio       = Column(Integer, nullable=False, index=True)
    fecha_hora_inicio = Column(DateTime, nullable=False, index=True)
    duracion_minutos  = Column(Integer, default=45)
    estado            = Column(Enum(EstadoTurno), default=EstadoTurno.pendiente, index=True)
    observaciones     = Column(String)
    whatsapp_enviado  = Column(Boolean, default=False, index=True)
    google_event_id   = Column(String)   # ID del evento en Google Calendar
    paciente          = relationship("Paciente", back_populates="turnos")
    medico            = relationship("Medico",   back_populates="turnos")

    __table_args__ = (
        # Índice compuesto para el caso más común: agenda diaria por consultorio
        Index("ix_turnos_consultorio_fecha", "consultorio", "fecha_hora_inicio"),
        Index("ix_turnos_medico_fecha",      "medico_id",   "fecha_hora_inicio"),
    )


class User(Base):
    __tablename__ = "users"
    id            = Column(Integer, primary_key=True, index=True)
    username      = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    display_name  = Column(String, nullable=False)
    role          = Column(String, nullable=False, default="medico")  # "admin" o "medico"
    medico_id     = Column(Integer, ForeignKey("medicos.id"), nullable=True)
    must_change_password = Column(Boolean, nullable=False, default=False)
    # 2FA TOTP (opcional). El secret queda cifrado. Activar requiere validar un código.
    totp_secret     = Column(EncryptedStr)
    totp_enabled    = Column(Boolean, nullable=False, default=False)
    medico        = relationship("Medico")


class RefreshToken(Base):
    """
    Token de larga duración (30 días) que permite obtener nuevos access_tokens
    sin re-login. Rotamos en cada uso (detección de reuso → revocar familia).

    Se guarda hash SHA-256 del token, nunca el token en claro. Cada refresh
    genera un token nuevo y revoca el anterior (one-time use).
    """
    __tablename__ = "refresh_tokens"
    id            = Column(Integer, primary_key=True, index=True)
    user_id       = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token_hash    = Column(String, unique=True, nullable=False, index=True)
    issued_at     = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at    = Column(DateTime, nullable=False, index=True)
    revoked_at    = Column(DateTime)                       # None si sigue activo
    replaced_by   = Column(Integer, ForeignKey("refresh_tokens.id"))   # detección de reuso
    user_agent    = Column(String)
    ip            = Column(String)

    user          = relationship("User")


class AuditLog(Base):
    """
    Registro inmutable de eventos sensibles. Requerido por Ley 25.326 / HIPAA para
    trazabilidad de accesos y modificaciones sobre datos de salud.

    action: string corto ("login.ok", "paciente.create", ...).
    entity_type / entity_id: objeto afectado (puede ser None para eventos como login).
    details: JSON-encoded string con campos adicionales (diff resumido, motivo, etc).
    ip: IP del cliente según X-Forwarded-For (detrás de proxy) o request.client.
    """
    __tablename__ = "audit_log"
    id           = Column(Integer, primary_key=True, index=True)
    timestamp    = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    user_id      = Column(Integer, index=True)        # sin FK: queremos preservar el log aunque se borre el usuario
    username     = Column(String, index=True)         # desnormalizado (el user_id puede ya no existir)
    action       = Column(String, nullable=False, index=True)
    entity_type  = Column(String, index=True)
    entity_id    = Column(Integer, index=True)
    details      = Column(Text)
    ip           = Column(String)

    __table_args__ = (
        Index("ix_audit_ts_action", "timestamp", "action"),
    )
