#!/usr/bin/env python3
"""
=============================================================================
VilcoSystem - Asistente Conversacional Decisional y Documental
Líder Técnico: Cristian Villa
Motor: Telegram Bot -> Gemini Multimodal (Auto-Discovery + Resiliencia 503) -> gTTS
Persistencia: Turso Cloud libSQL / SQLite Local
Módulos: task_database (Directorio, Solicitudes, Bitácora) + document_parser
=============================================================================
"""

import os
import sys
import io
import time
import json
import asyncio
import logging
import threading
import base64
from typing import Optional, List, Dict, Any, Tuple
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

load_dotenv()

# Variables de Entorno
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PORT = int(os.getenv("PORT", "10000"))

# Configuración de Logging
logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("VilcoVoiceAssistant")

# El chequeo estricto se delega a main() para permitir que el servidor HTTP
# arranque de inmediato y Render apruebe el despliegue sin bootloops.

# Importar submódulos de VilcoSystem
import task_database
import document_parser

# =============================================================================
# Servidor Web & Portal Operativo para Render y Monitoreo Local
# =============================================================================
class VilcoPortalServerHandler(BaseHTTPRequestHandler):
    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]

        # 1. Rutas de salud y raíz
        if path in ("/", "/index.html"):
            portal_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portal_tareas.html")
            if os.path.exists(portal_path):
                with open(portal_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(content)
                return
            else:
                msg = "VilcoSystem Portal Operativo Activo (Render Web Service)".encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(msg)
                return

        elif path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._send_cors_headers()
            self.end_headers()
            info_db = task_database.obtener_info_fuente_datos()
            resp = json.dumps({
                "status": "ok",
                "app": "VilcoVoiceAssistant",
                "portal": "enabled",
                "database_engine": info_db.get("motor"),
                "database_source": info_db.get("fuente"),
                "database_tables": info_db.get("tablas")
            })
            self.wfile.write(resp.encode("utf-8"))
            return

        elif path in ("/api/fuente", "/api/database"):
            try:
                info = task_database.obtener_info_fuente_datos()
                payload = json.dumps({"status": "success", "fuente_datos": info}, default=str)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(payload.encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
            return

        # 2. API de Tareas
        elif path == "/api/tareas":
            try:
                tareas = task_database.obtener_todas_las_tareas()
                payload = json.dumps({"status": "success", "tareas": tareas}, default=str)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(payload.encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "error": str(e)}).encode("utf-8"))
            return

        # 3. API de Métricas Dashboard
        elif path == "/api/metricas":
            try:
                metrics = task_database.obtener_metricas_dashboard()
                payload = json.dumps({"status": "success", "metricas": metrics}, default=str)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(payload.encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "error": str(e)}).encode("utf-8"))
            return

        # 4. Detalle de tarea por ID: /api/tareas/REQ-001
        elif path.startswith("/api/tareas/"):
            tarea_id = path.replace("/api/tareas/", "").strip()
            tarea = task_database.obtener_tarea_por_id(tarea_id)
            if tarea:
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "tarea": tarea}, default=str).encode("utf-8"))
            else:
                self.send_response(404)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": f"Tarea {tarea_id} no encontrada"}).encode("utf-8"))
            return

        # 5. Exportar CSV
        elif path == "/api/export/csv":
            try:
                tareas = task_database.obtener_todas_las_tareas()
                output = io.StringIO()
                output.write("\ufeff")
                output.write("ID,Titulo,Solicitante,Cargo,Area,Prioridad,Criticidad,Estado,Avance,Fecha_Registro,Fecha_Limite\n")
                for t in tareas:
                    tit = t.get('titulo', '').replace('"', '""')
                    sol = t.get('solicitante', '').replace('"', '""')
                    output.write(f'{t.get("id")},"{tit}","{sol}",{t.get("cargo_solicitante")},{t.get("area")},{t.get("prioridad")},{t.get("criticidad")},{t.get("estado")},{t.get("porcentaje_avance", 0)},{t.get("created_at")},{t.get("fecha_limite")}\n')
                csv_bytes = output.getvalue().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="reporte_tareas_vilcosystem.csv"')
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(csv_bytes)
            except Exception as e:
                self.send_response(500)
                self.end_headers()
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        path = self.path.split("?")[0]
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            data = {}

        if path == "/api/actualizar_avance":
            tarea_id = data.get("tarea_id")
            comentario = data.get("comentario", "")
            nuevo_estado = data.get("nuevo_estado")
            porcentaje = data.get("porcentaje")
            query_sql = data.get("query_sql")
            doc_ref = data.get("doc_referencia")

            if not tarea_id or not comentario:
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": "tarea_id y comentario son obligatorios"}).encode("utf-8"))
                return

            res = task_database.actualizar_avance(
                tarea_id=tarea_id,
                comentario=comentario,
                nuevo_estado=nuevo_estado,
                porcentaje=porcentaje,
                query_sql=query_sql,
                doc_referencia=doc_ref
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success", "tarea": res}, default=str).encode("utf-8"))
            return

        elif path in ("/api/voz", "/api/audio"):
            try:
                audio_b64 = data.get("audio_base64")
                mime_type = data.get("mime_type", "audio/webm")
                texto_dictado = data.get("texto")
                api_key = data.get("gemini_api_key") or data.get("api_key")

                if not audio_b64 and not texto_dictado:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self._send_cors_headers()
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "error", "message": "Se requiere audio_base64 o texto"}).encode("utf-8"))
                    return

                audio_bytes = base64.b64decode(audio_b64) if audio_b64 else None
                resultado = procesar_instruccion_multimodal(
                    audio_bytes=audio_bytes,
                    mime_type=mime_type,
                    texto=texto_dictado,
                    gemini_api_key=api_key
                )

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps(resultado, default=str).encode("utf-8"))
            except Exception as e:
                logger.exception(f"Error procesando voz en /api/voz: {e}")
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "error": str(e)}).encode("utf-8"))
            return

        elif path == "/api/crear_solicitud":
            res = task_database.crear_solicitud(
                titulo=data.get("titulo", "Nueva Solicitud"),
                solicitante=data.get("solicitante", "Cristian Villa"),
                cargo_solicitante=data.get("cargo_solicitante", "SUBGERENTE"),
                area=data.get("area", "OPERACIONES"),
                descripcion=data.get("descripcion", ""),
                prioridad=data.get("prioridad", "MEDIA"),
                criticidad=data.get("criticidad", "MODERADA"),
                justificacion_ia=data.get("justificacion_ia", "Registrado desde Portal Web"),
                fecha_limite=data.get("fecha_limite")
            )
            self.send_response(201)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success", "tarea": res}, default=str).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        pass


def start_health_server(port: int) -> None:
    try:
        server = HTTPServer(("0.0.0.0", port), VilcoPortalServerHandler)
        logger.info(f"Portal Web y Servidor HTTP de VilcoSystem activo en puerto {port}.")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Error iniciando servidor HTTP en puerto {port}: {e}")


# =============================================================================
# Telegram & Google GenAI
# =============================================================================
from telegram import Update, constants
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from google import genai
from google.genai import types
from gtts import gTTS

gemini_client = None

def get_gemini_client(api_key: Optional[str] = None):
    global gemini_client
    
    # 1. Priorizar clave pasada explícitamente en la petición
    key = api_key
    if key:
        key = key.strip().strip("'").strip('"')
    
    # 2. Si no, buscar en variables de entorno con múltiples alias y limpieza de comillas
    if not key:
        for var_name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_KEY", "GOOGLE_GENAI_API_KEY"):
            val = os.getenv(var_name)
            if val and val.strip():
                key = val.strip().strip("'").strip('"')
                break

    if not key:
        # Intento de instanciar cliente por defecto si el entorno ya tiene credenciales de Google
        try:
            gemini_client = genai.Client()
            return gemini_client
        except Exception:
            return None

    try:
        gemini_client = genai.Client(api_key=key)
        os.environ["GEMINI_API_KEY"] = key
        return gemini_client
    except Exception as e:
        logger.error(f"Error inicializando Google GenAI con API Key: {e}")
        return None


# =============================================================================
# Descubrimiento Dinámico de Modelos Activos (Auto-Discovery)
# =============================================================================
DEFAULT_FALLBACK_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-pro-preview",
    "gemini-2.5-pro"
]

ACTIVE_MODELS_CACHE: List[str] = []


def rank_model(name: str) -> int:
    """Prioriza modelos rápidos (Flash 3.8/3.5) y luego modelos de alta capacidad (Pro)."""
    n = name.lower()
    if "3.8-flash" in n:
        return 1
    if "flash" in n and "3" in n:
        return 2
    if "3.1-pro" in n or "3-pro" in n:
        return 3
    if "2.5-pro" in n:
        return 4
    if "flash" in n:
        return 5
    return 10


def discover_active_models() -> List[str]:
    """Consulta la API de Google para detectar qué modelos están activos y disponibles."""
    global ACTIVE_MODELS_CACHE
    if ACTIVE_MODELS_CACHE:
        return ACTIVE_MODELS_CACHE

    try:
        logger.info("Descubriendo modelos activos en la cuenta de Google AI Studio...")
        available = []
        client = get_gemini_client()
        if not client:
            return DEFAULT_FALLBACK_MODELS
        for m in client.models.list():
            m_name = m.name.replace("models/", "")
            # Descartar modelos de embedding, imagen pura o experimentales no conversacionales
            if "gemini" in m_name and not any(x in m_name for x in ["embedding", "imagen", "veo", "lyria"]):
                # Descartar versiones retiradas que sabemos que dan 404
                if not any(deprecated in m_name for deprecated in ["2.0-flash", "2.5-flash", "1.5-flash"]):
                    available.append(m_name)

        if available:
            sorted_models = sorted(available, key=rank_model)
            logger.info(f"Modelos descubiertos y priorizados: {sorted_models}")
            ACTIVE_MODELS_CACHE = sorted_models
            return sorted_models
    except Exception as e:
        logger.warning(f"No se pudo consultar list_models ({e}). Usando lista por defecto.")

    ACTIVE_MODELS_CACHE = DEFAULT_FALLBACK_MODELS
    return DEFAULT_FALLBACK_MODELS


SYSTEM_INSTRUCTION = (
    "Eres el Asistente Decisional y de Gestión Técnica de VilcoSystem, al servicio directo "
    "de Cristian Villa (Líder Técnico de VilcoSystem).\n\n"
    "Tus responsabilidades principales son:\n"
    "1. RECONOCIMIENTO Y GESTIÓN DE PERSONAL: VilcoSystem cuenta con un directorio de personal "
    "(gerentes, subgerentes, jefaturas). Puedes registrar o actualizar personas usando registrar_personal "
    "o consultar el directorio con consultar_directorio. Cuando Cristian mencione un nombre (ej. 'Valerio'), "
    "reconoce su cargo, área y nivel jerárquico.\n"
    "2. RECEPCIÓN Y CATALOGACIÓN AUTOMÁTICA DE SOLICITUDES: Cristian te dictará por voz o texto "
    "los pedidos recibidos de distintas áreas, subgerentes y el Gerente General. Tú debes catalogar "
    "automáticamente el solicitante, cargo, área, prioridad (URGENTE/ALTA/MEDIA/BAJA) y criticidad "
    "(CRITICA/ALTA/MODERADA/LEVE) considerando el impacto en el negocio (corte de servicios, facturación "
    "o recaudación son CRÍTICOS). Guarda la solicitud llamando a registrar_solicitud.\n"
    "3. ASESORÍA DECISIONAL ('¿Cuál atender primero y por qué?'): Cuando Cristian te pregunte qué atender primero, "
    "consulta las tareas pendientes llamando a consultar_prioridades y calcula la mejor recomendación ponderando: "
    "(a) Jerarquía (Gerente General > Subgerente > Jefes > Operativo), (b) Criticidad de negocio, "
    "(c) Plazos y dependencias. Explica con claridad el motivo de tu sugerencia.\n"
    "4. BITÁCORA Y QUERIES TÉCNICAS: Permite actualizar el avance de cada pedido registrando comentarios, "
    "queries SQL ejecutadas o scripts, porcentajes de avance y cambio de estados (EN_PROCESO, BLOQUEADO, COMPLETADO).\n"
    "5. CONSULTA DOCUMENTAL: Cuando Cristian pregunte por el contenido de un archivo adjunto (Word, Excel, Script, PDF) "
    "relacionado a un pedido, llama a consultar_documento_tarea para inspeccionar el texto extraído y responder con exactitud.\n"
    "6. REPORTES POR FECHAS: Cuando solicite resúmenes de tareas realizadas o en curso en un rango de fechas, "
    "llama a generar_reporte_periodo y entrega un informe ejecutivo claro.\n\n"
    "Estilo de respuesta: Profesional, ejecutivo, directo y conversacional en español, ideal para ser escuchado en audio. "
    "Evita markdown confuso que suene mal al sintetizarse a voz."
)


# =============================================================================
# Declaración de Herramientas (Function Calling)
# =============================================================================
tool_registrar_solicitud = {
    "name": "registrar_solicitud",
    "description": "Registra una nueva solicitud catalogando automáticamente su prioridad, criticidad y área.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "titulo": {"type": "STRING", "description": "Título o resumen ejecutivo de la solicitud"},
            "solicitante": {"type": "STRING", "description": "Nombre de la persona que solicitó el requerimiento"},
            "cargo_solicitante": {
                "type": "STRING",
                "description": "Cargo del solicitante: GERENTE_GENERAL, SUBGERENTE, JEFE_AREA, OPERATIVO u OTRO"
            },
            "area": {"type": "STRING", "description": "Área de origen: GERENCIA, OPERACIONES, TI, FINANZAS, FACTURACION, etc."},
            "descripcion": {"type": "STRING", "description": "Detalle completo de lo solicitado"},
            "prioridad": {"type": "STRING", "enum": ["URGENTE", "ALTA", "MEDIA", "BAJA"]},
            "criticidad": {"type": "STRING", "enum": ["CRITICA", "ALTA", "MODERADA", "LEVE"]},
            "justificacion_ia": {"type": "STRING", "description": "Justificación del por qué se asignó esa prioridad y criticidad"},
            "fecha_limite": {"type": "STRING", "description": "Fecha y hora límite si se mencionó (YYYY-MM-DD o texto descriptivo)"}
        },
        "required": ["titulo", "solicitante", "cargo_solicitante", "area", "descripcion", "prioridad", "criticidad"]
    }
}

tool_actualizar_avance = {
    "name": "actualizar_avance",
    "description": "Registra una actualización en la bitácora de una tarea, con comentarios, query SQL, avance porcentual o cambio de estado.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "tarea_id_o_termino": {"type": "STRING", "description": "ID de la tarea (ej. REQ-001) o palabras clave del título"},
            "comentario": {"type": "STRING", "description": "Comentario o bitácora de lo que se avanzó"},
            "nuevo_estado": {"type": "STRING", "enum": ["PENDIENTE", "EN_PROCESO", "BLOQUEADO", "REVISION", "COMPLETADO"]},
            "porcentaje": {"type": "INTEGER", "description": "Porcentaje de avance estimado (0 a 100)"},
            "query_sql": {"type": "STRING", "description": "Consulta SQL, script o comando técnico ejecutado si aplica"},
            "doc_referencia": {"type": "STRING", "description": "Nombre o enlace del documento de entrega o recepción"}
        },
        "required": ["tarea_id_o_termino", "comentario"]
    }
}

tool_consultar_prioridades = {
    "name": "consultar_prioridades",
    "description": "Obtiene la lista actual de tareas pendientes y en curso ordenadas por impacto para asesorar a Cristian sobre qué atender primero.",
    "parameters": {"type": "OBJECT", "properties": {}}
}

tool_consultar_documento_tarea = {
    "name": "consultar_documento_tarea",
    "description": "Inspecciona el contenido de documentos (Excel, Word, Scripts, PDF) vinculados a una tarea para responder preguntas específicas.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "tarea_id_o_termino": {"type": "STRING", "description": "ID de la tarea (ej. REQ-001) o título relacionado"},
            "pregunta": {"type": "STRING", "description": "Pregunta específica sobre lo que se desea saber o auditar del documento"}
        },
        "required": ["tarea_id_o_termino", "pregunta"]
    }
}

tool_generar_reporte_periodo = {
    "name": "generar_reporte_periodo",
    "description": "Genera un reporte de tareas y solicitudes atendidas o en curso en un rango de fechas.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "fecha_inicio": {"type": "STRING", "description": "Fecha inicial YYYY-MM-DD"},
            "fecha_fin": {"type": "STRING", "description": "Fecha final YYYY-MM-DD"},
            "estado": {"type": "STRING", "description": "Opcional: filtrar por estado (COMPLETADO, EN_PROCESO, PENDIENTE)"}
        },
        "required": ["fecha_inicio", "fecha_fin"]
    }
}

tool_registrar_personal = {
    "name": "registrar_personal",
    "description": "Registra o actualiza a un miembro del equipo de VilcoSystem (gerentes, subgerentes, jefaturas) en el directorio.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "nombre": {"type": "STRING", "description": "Nombre de la persona (ej. Valerio, Ing. Carlos Mendoza)"},
            "cargo": {"type": "STRING", "description": "Cargo institucional (ej. Gerente General, Subgerente de Operaciones, Jefe de Facturacion)"},
            "nivel_jerarquico": {
                "type": "INTEGER",
                "description": "Nivel jerárquico: 1 (Gerente General), 2 (Subgerente), 3 (Jefatura), 4 (Operativo)"
            },
            "area": {"type": "STRING", "description": "Área de la empresa (ej. Gerencia, Operaciones, TI, Finanzas)"},
            "contacto": {"type": "STRING", "description": "Opcional: teléfono o email"}
        },
        "required": ["nombre", "cargo", "area"]
    }
}

tool_consultar_directorio = {
    "name": "consultar_directorio",
    "description": "Consulta la lista de personas y cargos registrados en el directorio institucional de VilcoSystem.",
    "parameters": {"type": "OBJECT", "properties": {}}
}

AVAILABLE_TOOLS = [
    tool_registrar_solicitud,
    tool_actualizar_avance,
    tool_consultar_prioridades,
    tool_consultar_documento_tarea,
    tool_generar_reporte_periodo,
    tool_registrar_personal,
    tool_consultar_directorio
]


# =============================================================================
# Ejecutor Local de Tools
# =============================================================================
def execute_tool_call(tool_name: str, args: Dict[str, Any]) -> Any:
    logger.info(f"Ejecutando tool '{tool_name}' con argumentos: {args}")
    try:
        if tool_name == "registrar_solicitud":
            res = task_database.crear_solicitud(
                titulo=args.get("titulo", "Solicitud sin título"),
                solicitante=args.get("solicitante", "No especificado"),
                cargo_solicitante=args.get("cargo_solicitante", "OPERATIVO"),
                area=args.get("area", "GENERAL"),
                descripcion=args.get("descripcion", ""),
                prioridad=args.get("prioridad", "MEDIA"),
                criticidad=args.get("criticidad", "MODERADA"),
                justificacion_ia=args.get("justificacion_ia", ""),
                fecha_limite=args.get("fecha_limite")
            )
            return {"status": "success", "tarea": res}

        elif tool_name == "actualizar_avance":
            res = task_database.actualizar_avance(
                tarea_id=args.get("tarea_id_o_termino", ""),
                comentario=args.get("comentario", ""),
                nuevo_estado=args.get("nuevo_estado"),
                porcentaje=args.get("porcentaje"),
                query_sql=args.get("query_sql"),
                doc_referencia=args.get("doc_referencia")
            )
            if res:
                return {"status": "success", "tarea_actualizada": res}
            return {"status": "error", "message": f"No se encontró la tarea '{args.get('tarea_id_o_termino')}'"}

        elif tool_name == "consultar_prioridades":
            pendientes = task_database.obtener_tareas_pendientes()
            return {"status": "success", "total_pendientes": len(pendientes), "tareas": pendientes}

        elif tool_name == "consultar_documento_tarea":
            termino = args.get("tarea_id_o_termino", "")
            tarea = task_database.obtener_tarea_por_id(termino) or task_database.buscar_tarea_por_termino(termino)
            if not tarea:
                return {"status": "error", "message": f"No se encontró la tarea '{termino}'"}

            docs = task_database.obtener_documentos_de_tarea(tarea["id"])
            if not docs:
                return {"status": "no_docs", "message": f"La tarea {tarea['id']} no tiene documentos adjuntos registrados."}

            resumen_docs = []
            for d in docs:
                resumen_docs.append({
                    "nombre": d["nombre_archivo"],
                    "tipo": d["tipo_archivo"],
                    "contenido": d["texto_extraido"][:10000]
                })
            return {
                "status": "success",
                "tarea_id": tarea["id"],
                "titulo_tarea": tarea["titulo"],
                "pregunta": args.get("pregunta"),
                "documentos": resumen_docs
            }

        elif tool_name == "generar_reporte_periodo":
            reporte = task_database.obtener_reporte_por_fechas(
                fecha_inicio=args.get("fecha_inicio", "2000-01-01"),
                fecha_fin=args.get("fecha_fin", "2099-12-31"),
                estado=args.get("estado")
            )
            return {"status": "success", "total": len(reporte), "reporte": reporte}

        elif tool_name == "registrar_personal":
            res = task_database.guardar_personal(
                nombre=args.get("nombre", ""),
                cargo=args.get("cargo", ""),
                nivel_jerarquico=args.get("nivel_jerarquico", 3),
                area=args.get("area", "GENERAL"),
                contacto=args.get("contacto")
            )
            return {"status": "success", "miembro_registrado": res}

        elif tool_name == "consultar_directorio":
            directorio = task_database.listar_directorio()
            return {"status": "success", "total_miembros": len(directorio), "directorio": directorio}

        else:
            return {"status": "error", "message": f"Tool '{tool_name}' desconocida"}
    except Exception as e:
        logger.exception(f"Error ejecutando tool '{tool_name}': {e}")
        return {"status": "error", "error": str(e)}


# =============================================================================
# Invocación con Reintentos Progresivos y Fallback Dinámico
# =============================================================================
def _generate_with_fallback(
    contents: List[Any],
    config: types.GenerateContentConfig,
    gemini_api_key: Optional[str] = None
) -> Tuple[Any, str]:
    """
    Ejecuta la llamada a Gemini utilizando modelos descubiertos dinámicamente.
    Aplica reintentos progresivos (backoff) ante 503/429 y conmuta entre modelos activos.
    """
    client = get_gemini_client(gemini_api_key)
    if not client:
        raise ValueError(
            "La clave de API de Gemini (GEMINI_API_KEY) no está configurada en Render ni fue enviada desde el portal. "
            "Por favor agrégala en el panel de Render (Environment > GEMINI_API_KEY) o ingrésala en el botón '🔌 Conexión' del portal."
        )

    models_to_try = discover_active_models()
    last_error = None

    for model_name in models_to_try:
        # Hasta 3 reintentos con backoff progresivo (1.5s, 3.0s)
        for attempt in range(3):
            try:
                logger.info(f"Llamando a Gemini con modelo '{model_name}' (intento {attempt + 1})...")
                response = client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config
                )
                if response:
                    return response, model_name
            except Exception as e:
                err_str = str(e)
                last_error = e
                logger.warning(f"Error con modelo '{model_name}' (intento {attempt + 1}): {err_str[:120]}")

                # Si el modelo no existe o está retirado (404), pasar de inmediato al siguiente
                if "404" in err_str or "NOT_FOUND" in err_str:
                    break

                # Si es sobrecarga temporal (503 UNAVAILABLE) o cuota (429), pausar antes de reintentar
                if "503" in err_str or "UNAVAILABLE" in err_str or "429" in err_str:
                    sleep_time = 1.5 * (attempt + 1)
                    logger.info(f"Pausa defensiva de {sleep_time}s por alta demanda...")
                    time.sleep(sleep_time)
                    continue
                else:
                    break

    raise RuntimeError(f"Error de ejecución con Gemini: {last_error}")


def _run_gemini_turn(input_contents: List[Any], gemini_api_key: Optional[str] = None) -> str:
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        temperature=0.4,
        tools=[{"function_declarations": AVAILABLE_TOOLS}]
    )

    current_contents = list(input_contents)

    for turn in range(4):
        response, used_model = _generate_with_fallback(current_contents, config, gemini_api_key=gemini_api_key)

        function_calls = []
        if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if hasattr(part, "function_call") and part.function_call:
                    function_calls.append(part.function_call)

        if not function_calls:
            return response.text or "Solicitud procesada correctamente."

        current_contents.append(response.candidates[0].content)
        tool_response_parts = []
        for fc in function_calls:
            fname = fc.name
            fargs = dict(fc.args) if fc.args else {}
            fresult = execute_tool_call(fname, fargs)
            tool_response_parts.append(
                types.Part.from_function_response(
                    name=fname,
                    response={"result": fresult}
                )
            )

        current_contents.append(types.Content(parts=tool_response_parts))

    return "He procesado las consultas y actualizado la base de datos de VilcoSystem."


def _synthesize_voice(text: str) -> io.BytesIO:
    clean_text = text.replace("*", "").replace("#", "").replace("`", "").replace("_", "").strip()
    if not clean_text:
        clean_text = "He procesado tu requerimiento correctamente."

    tts = gTTS(text=clean_text[:600], lang="es", slow=False)
    buffer = io.BytesIO()
    tts.write_to_fp(buffer)
    buffer.seek(0)
    buffer.name = "response.mp3"
    return buffer


def procesar_instruccion_multimodal(
    audio_bytes: Optional[bytes] = None,
    mime_type: str = "audio/webm",
    texto: Optional[str] = None,
    gemini_api_key: Optional[str] = None
) -> Dict[str, Any]:
    """
    Procesa notas de voz o instrucciones de texto provenientes del Portal Web o de Telegram.
    Pasa la entrada a Gemini Multimodal con Function Calling activo para que cataloge o actualice tareas
    directamente en las tablas de Turso Cloud (solicitudes_tareas, bitacora_avance, directorio_personal).
    """
    logger.info(f"Procesando instrucción web multimodal (audio_bytes={bool(audio_bytes)}, texto={bool(texto)})...")
    contents = []

    if audio_bytes:
        clean_mime = mime_type.split(";")[0].strip().lower()
        if not clean_mime or clean_mime == "audio/opus":
            clean_mime = "audio/webm"
        contents.append(types.Part.from_bytes(data=audio_bytes, mime_type=clean_mime))
        prompt_context = "Escucha atentamente este audio de Cristian Villa dictado desde el Portal de Tareas de VilcoSystem. "
        if texto:
            prompt_context += f"Transcripción previa del navegador: '{texto}'. "
        prompt_context += "Identifica la intención (registrar nueva solicitud, actualizar avance con query o consultar prioridades), cataloga adecuadamente y ejecuta la tool correspondiente."
        contents.append(prompt_context)
    elif texto:
        contents.append(f"Instrucción de Cristian Villa desde el Portal de Tareas: {texto}")
    else:
        raise ValueError("Se requiere audio_bytes o texto para procesar la instrucción.")

    # Ejecutar ciclo de razonamiento con Gemini y Function Calling
    texto_respuesta = _run_gemini_turn(contents, gemini_api_key=gemini_api_key)

    # Generar audio de respuesta con gTTS en base64 para reproducir en el navegador
    audio_base64 = None
    try:
        audio_stream = _synthesize_voice(texto_respuesta)
        audio_base64 = base64.b64encode(audio_stream.read()).decode("utf-8")
    except Exception as e_tts:
        logger.warning(f"No se pudo sintetizar voz de respuesta para el portal: {e_tts}")

    # Obtener listado fresco de tareas y métricas de Turso Cloud
    tareas_frescas = task_database.obtener_todas_las_tareas()
    metricas_frescas = task_database.obtener_metricas_dashboard()

    return {
        "status": "success",
        "respuesta": texto_respuesta,
        "audio_base64": audio_base64,
        "tareas": tareas_frescas,
        "metricas": metricas_frescas,
        "total_tareas": len(tareas_frescas)
    }


# =============================================================================
# Handlers de Telegram
# =============================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    welcome_text = (
        "👋 ¡Bienvenido, Cristian! Soy tu **Asistente Decisional y de Gestión Técnica de VilcoSystem**.\n\n"
        "🎙️ **Capacidades habilitadas:**\n"
        "1. **Directorio y Organigrama:** Registra a gerentes y jefaturas (ej. *'Registra a Valerio como Subgerente de Operaciones'*).\n"
        "2. **Recepción por Voz:** Dicta los pedidos recibidos. Reconoceré al solicitante y catalogaré prioridad y criticidad.\n"
        "3. **Asesoría de Prioridades:** Pregúntame *'¿Qué debo atender primero y por qué?'*.\n"
        "4. **Bitácora y Queries:** Dicta avances, queries SQL o estados.\n"
        "5. **Recepción Documental:** Adjunta archivos Word, Excel, Scripts o PDF para analizarlos.\n"
        "6. **Reportes:** Solicita resúmenes ejecutivos por rango de fechas."
    )
    await update.message.reply_text(welcome_text, parse_mode=constants.ParseMode.MARKDOWN)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    voice_obj = message.voice or message.audio
    if not voice_obj:
        return

    logger.info(f"Nota de voz recibida ({voice_obj.duration}s)...")
    await message.reply_chat_action(action=constants.ChatAction.RECORD_VOICE)

    try:
        telegram_file = await context.bot.get_file(voice_obj.file_id)
        audio_bytes = bytes(await telegram_file.download_as_bytearray())

        await message.reply_chat_action(action=constants.ChatAction.TYPING)
        contents = [
            types.Part.from_bytes(data=audio_bytes, mime_type="audio/ogg"),
            "Escucha este audio de Cristian Villa y ejecuta las acciones requeridas."
        ]
        ai_response_text = await asyncio.to_thread(_run_gemini_turn, contents)

        await message.reply_text(ai_response_text, reply_to_message_id=message.message_id)

        try:
            await message.reply_chat_action(action=constants.ChatAction.RECORD_VOICE)
            voice_buf = await asyncio.to_thread(_synthesize_voice, ai_response_text)
            await message.reply_voice(
                voice=voice_buf,
                caption="🎙️ *Respuesta de voz*",
                parse_mode=constants.ParseMode.MARKDOWN,
                reply_to_message_id=message.message_id
            )
        except Exception as e:
            logger.warning(f"Error generando audio TTS: {e}")

    except Exception as e:
        logger.exception(f"Error procesando nota de voz: {e}")
        err_str = str(e)
        if "503" in err_str or "UNAVAILABLE" in err_str:
            friendly_msg = "⚠️ Los servidores de Google Gemini están experimentando una saturación temporal (503). Por favor reenvía tu audio en 10-15 segundos."
        elif "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
            friendly_msg = "⚠️ Límite de cuota temporal alcanzado en la API. Por favor espera unos momentos."
        else:
            friendly_msg = f"⚠️ Ocurrió una incidencia:\n{err_str[:250]}"

        await message.reply_text(friendly_msg, reply_to_message_id=message.message_id)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    text = message.text
    if not text:
        return

    logger.info(f"Mensaje de texto recibido: '{text[:60]}...'")
    await message.reply_chat_action(action=constants.ChatAction.TYPING)
    try:
        contents = [text]
        ai_response_text = await asyncio.to_thread(_run_gemini_turn, contents)
        await message.reply_text(ai_response_text, reply_to_message_id=message.message_id)

        try:
            await message.reply_chat_action(action=constants.ChatAction.RECORD_VOICE)
            voice_buf = await asyncio.to_thread(_synthesize_voice, ai_response_text)
            await message.reply_voice(voice=voice_buf, caption="🎙️ *Audio respuesta*", reply_to_message_id=message.message_id)
        except Exception as e:
            logger.warning(f"Error generando audio: {e}")
    except Exception as e:
        logger.exception(f"Error en mensaje de texto: {e}")
        err_str = str(e)
        if "503" in err_str or "UNAVAILABLE" in err_str:
            friendly_msg = "⚠️ Los servidores de Google Gemini están experimentando una saturación temporal (503). Por favor reenvía tu mensaje en 10-15 segundos."
        elif "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
            friendly_msg = "⚠️ Límite de cuota alcanzado en la API. Por favor espera un momento."
        else:
            friendly_msg = f"⚠️ Ocurrió una incidencia: {err_str[:250]}"

        await message.reply_text(friendly_msg, reply_to_message_id=message.message_id)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    doc = message.document
    if not doc:
        return

    filename = doc.file_name or "archivo_sin_nombre"
    filesize = doc.file_size or 0
    caption = message.caption or ""

    logger.info(f"Documento recibido: '{filename}' ({filesize} bytes), caption: '{caption}'")
    await message.reply_chat_action(action=constants.ChatAction.TYPING)

    try:
        telegram_file = await context.bot.get_file(doc.file_id)
        file_bytes = bytes(await telegram_file.download_as_bytearray())

        tipo_detectado, texto_extraido, resumen = document_parser.extract_content(file_bytes, filename)
        logger.info(f"Extracción completada: {tipo_detectado}, resumen: {resumen}")

        tarea_asociada = None
        if caption:
            tarea_asociada = task_database.buscar_tarea_por_termino(caption)

        if not tarea_asociada:
            tarea_asociada = task_database.obtener_ultima_tarea()

        if not tarea_asociada:
            tarea_asociada = task_database.crear_solicitud(
                titulo=f"Revisión de documento: {filename}",
                solicitante="Cristian Villa",
                cargo_solicitante="LIDER_TECNICO",
                area="DOCUMENTACION",
                descripcion=f"Documento adjunto recibido: {filename}. Resumen: {resumen}",
                prioridad="MEDIA",
                criticidad="MODERADA"
            )

        doc_id = task_database.registrar_documento(
            tarea_id=tarea_asociada["id"],
            nombre_archivo=filename,
            tipo_archivo=tipo_detectado,
            tamano_bytes=filesize,
            texto_extraido=texto_extraido,
            resumen=resumen
        )

        resp_msg = (
            f"📎 **Documento indexado con éxito (Doc #{doc_id})**\n\n"
            f"• **Archivo:** `{filename}` ({tipo_detectado})\n"
            f"• **Asociado a:** `{tarea_asociada['id']} - {tarea_asociada['titulo']}`\n"
            f"• **Análisis:** {resumen}\n\n"
            f"💡 *Ya puedes hacerme preguntas por voz o texto sobre el contenido de este documento.*"
        )
        await message.reply_text(resp_msg, parse_mode=constants.ParseMode.MARKDOWN, reply_to_message_id=message.message_id)

    except Exception as e:
        logger.exception(f"Error procesando documento: {e}")
        await message.reply_text(f"⚠️ Error al indexar documento: {e}", reply_to_message_id=message.message_id)


def main() -> None:
    logger.info("Iniciando Asistente Decisional y Documental VilcoSystem (v11.0.2 Auto-Discovery)...")
    # 1. Iniciar Servidor Web & Portal Operativo prioritariamente para Render
    http_thread = threading.Thread(target=start_health_server, args=(PORT,), daemon=True)
    http_thread.start()

    # 2. Validación defensiva de credenciales para producción
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    gemini_key = os.getenv("GEMINI_API_KEY")

    if not token or not gemini_key:
        logger.warning(
            "⚠️ ESPERANDO CONFIGURACIÓN: TELEGRAM_BOT_TOKEN o GEMINI_API_KEY aún no están definidas en Render. "
            "El servidor web y portal se mantendrán activos en el puerto para satisfacer los health checks."
        )
        while not (os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("GEMINI_API_KEY")):
            time.sleep(5)
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        gemini_key = os.getenv("GEMINI_API_KEY")

    # 3. Inicializar Google GenAI y descubrir modelos activos
    get_gemini_client()
    discover_active_models()

    # 4. Iniciar Bot de Telegram
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))

    logger.info("Polling de Telegram iniciado. Escuchando eventos...")
    try:
        app.run_polling(drop_pending_updates=True)
    except Exception as e_poll:
        logger.warning(f"Aviso en polling de Telegram: {e_poll}. Manteniendo servidor HTTP activo.")
        # Mantener el hilo principal vivo si run_polling finaliza
        while True:
            time.sleep(10)


if __name__ == "__main__":
    main()
