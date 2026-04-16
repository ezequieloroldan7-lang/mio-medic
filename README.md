# 🏥 MIO MEDIC — Sistema de Turnos

Sistema interno de gestión de turnos para **MIO MEDIC / MIE MEDIC**.

## Stack
- **Backend:** Python + FastAPI + SQLite
- **Frontend:** HTML + CSS + JavaScript (vanilla, responsive)
- **WhatsApp:** Twilio API
- **Deploy:** Render.com

---

## 🚀 Instalación local (desarrollo)

```bash
# 1. Instalar dependencias
cd backend
pip install -r ../requirements.txt

# 2. Migrar pacientes desde Excel (solo primera vez)
python migrate.py "ruta/al/Pacientes_MioMedic.xlsx"

# 3. Iniciar servidor
uvicorn main:app --reload --port 8000
```

Abrir en el navegador: **http://localhost:8000**

---

## ☁️ Deploy en Render.com

1. Subir el proyecto a **GitHub** (repositorio privado)
2. En [render.com](https://render.com) → **New Web Service**
3. Conectar el repositorio
4. Configurar:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Agregar variables de entorno:
   ```
   WHATSAPP_ACCESS_TOKEN    = EAAxxxxxxxxxxxxxxx
   WHATSAPP_PHONE_NUMBER_ID = 1234567890
   WHATSAPP_TEMPLATE_NAME   = recordatorio_turno
   WHATSAPP_TEMPLATE_LANG   = es_AR
   ```
6. Deploy → ¡Listo!

---

## 📱 WhatsApp Business (Meta Cloud API)

El sistema usa la **WhatsApp Cloud API de Meta** directo — sin intermediarios.

### Setup inicial

1. Ir a [developers.facebook.com/apps](https://developers.facebook.com/apps) y crear una app tipo **Business**.
2. Agregar el producto **WhatsApp** → vas a obtener un `Phone Number ID` de prueba gratis.
3. En **Business Settings → Users → System users** generar un **System User Access Token permanente** con los permisos `whatsapp_business_messaging` y `whatsapp_business_management`.
4. En **Meta Business Suite → WhatsApp → Plantillas de mensaje**, crear una plantilla llamada exactamente `recordatorio_turno` (o el nombre que pongas en `WHATSAPP_TEMPLATE_NAME`) con 4 variables en el cuerpo:

   ```
   Hola {{1}} 👋
   Te recordamos que mañana tenés turno en MIO MEDIC:
   📅 {{2}}
   👩‍⚕️ {{3}}
   🏥 {{4}}

   Respondé SI para confirmar o avisanos si necesitás cancelar.
   ```

   Las variables son, en orden: `1` paciente, `2` fecha/hora, `3` profesional, `4` especialidad.

5. Esperar aprobación de la plantilla (suele ser minutos).
6. Configurar las variables en `.env` (ver `.env.example`).

### Cómo funciona el scheduler

El sistema ejecuta un job cada hora que busca los turnos del día siguiente y envía la plantilla a cada paciente. Marca el turno con `whatsapp_enviado=True` para no duplicar.

> ⚠️ **Ventana de 24 hs**: Meta solo permite mensajes libres de texto dentro de 24 hs desde el último mensaje del usuario. Los recordatorios son mensajes iniciados por el negocio, por eso **deben** usar una plantilla pre-aprobada.

---

## 👩‍⚕️ Médicos cargados

| Nombre | Especialidad |
|---|---|
| Dra. María de los Ángeles Garrido | Ginecología |
| Dr. Carlos Pereyra | Cosmetología |
| Dr. Martín Rodríguez | Nutrición |
| Dra. Sofía Méndez | Sexología |
| Dra. Laura Fernández | Dermatología |

> Los médicos se pueden actualizar directamente en la base de datos o se puede agregar un panel de administración en una próxima versión.

---

## 📁 Estructura del proyecto

```
miomedic/
├── backend/
│   ├── main.py          ← App principal + scheduler WhatsApp
│   ├── database.py      ← Conexión SQLite
│   ├── models.py        ← Tablas de la BD
│   ├── schemas.py       ← Validación de datos
│   ├── whatsapp.py      ← Envío de mensajes Twilio
│   ├── migrate.py       ← Importar Excel → BD
│   └── routers/
│       ├── pacientes.py
│       ├── turnos.py
│       └── medicos.py
├── frontend/
│   ├── index.html       ← App principal
│   ├── css/styles.css
│   └── js/app.js
└── requirements.txt
```
