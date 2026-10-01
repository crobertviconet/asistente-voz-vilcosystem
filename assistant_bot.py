#!/usr/bin/env python3
"""
=============================================================================
VilcoSystem - Asistente Conversacional Decisional y Documental
Líder Técnico: Cristian Villa
Motor: Telegram Bot -> Gemini Multimodal (Fallback y Reintentos 503) -> gTTS
Módulos: task_database (SQLite/Turso) + document_parser (Word/Excel/PDF/Scripts)
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

if not TELEGRAM_BOT_TOKEN or not GEMINI_API_KEY:
    logger.critical("Faltan variables de entorno TELEGRAM_BOT_TOKEN o GEMINI_API_KEY.")
    sys.exit(1)

# Importar submódulos de VilcoSystem
import task_database
import document_parser

# =============================================================================
# Servidor HTTP para Render Free Plan
# =============================================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("VilcoSystem Decisional Voice Assistant is running OK".encode("utf-8"))

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()

    def log_message(self, format, *args):
        pass


def start_health_server(port: int) -> None:
    try:
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        logger.info(f"Servidor HTTP de salud activo en puerto {port}.")
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

try:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    logger.info("Cliente de Google GenAI inicializado con éxito.")
except Exception as e:
    logger.exception(f"Error inicializando Google GenAI: {e}")
    sys.exit(1)

# Cascada de modelos compatibles para tolerar saturaciones de servidores (503) o 404
CANDIDATE_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-3-flash"
]

SYSTEM_INSTRUCTION = (
    "Eres el Asistente Decisional y de Gestión Técnica de VilcoSystem, al servicio directo "
    "de Cristian Villa (Líder Técnico de VilcoSystem).\n\n"
    "Tus responsabilidades principales son:\n"
    "1. RECEPCIÓN Y CATALOGACIÓN AUTOMÁTICA DE SOLICITUDES: Cristian te dictará por voz o texto "
    "los pedidos recibidos de distintas áreas, subgerentes y el Gerente General. Tú debes catalogar "
    "automáticamente la jerarquía del solicitante, área, prioridad (URGENTE/ALTA/MEDIA/BAJA) y "
    "criticidad (CRITICA/ALTA/MODERADA/LEVE) considerando el impacto en el negocio (ej. corte de servicios, "
    "recaudación, auditoría o facturación son CRÍTICOS). Guarda la solicitud llamando a la función registrar_solicitud.\n"
    "2. ASESORÍA DECISIONAL ('¿Cuál atender primero y por qué?'): Cuando Cristian te pregunte qué atender primero, "
    "consulta las tareas pendientes llamando a consultar_prioridades y calcula la mejor recomendación ponderando: "
    "(a) Jerarquía (Gerente General > Subgerente > Jefes > Operativo), (b) Criticidad de negocio, "
    "(c) Plazos y dependencias. Explica con claridad el motivo de tu sugerencia.\n"
    "3. BITÁCORA Y QUERIES TÉCNICAS: Permite actualizar el avance de cada pedido registrando comentarios, "
    "queries SQL ejecutadas o scripts, porcentajes de avance y cambio de estados (EN_PROCESO, BLOQUEADO, COMPLETADO).\n"
    "4. CONSULTA DOCUMENTAL: Cuando Cristian pregunte por el contenido de un archivo adjunto (Word, Excel, Script, PDF) "
    "relacionado a un pedido, llama a consultar_documento_tarea para inspeccionar el texto extraído y responder con exactitud.\n"
    "5. REPORTES POR FECHAS: Cuando solicite resúmenes de tareas realizadas o en curso en un rango de fechas, "
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

AVAILABLE_TOOLS = [
    tool_registrar_solicitud,
    tool_actualizar_avance,
    tool_consultar_prioridades,
    tool_consultar_documento_tarea,
    tool_generar_reporte_periodo
]


# =============================================================================
# Ejecutor Local de Tools
# =============================================================================
def execute_tool_call(tool_name: str, args: Dict[str, Any]) -> Any:
    """Ejecuta la función Python correspondiente al Tool Call emitido por Gemini."""
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

        else:
            return {"status": "error", "message": f"Tool '{tool_name}' desconocida"}
    except Exception as e:
        logger.exception(f"Error ejecutando tool '{tool_name}': {e}")
        return {"status": "error", "error": str(e)}


# =============================================================================
# Invocación con Reintentos y Cascada de Fallback (Tolerancia a 503 / 429)
# =============================================================================
def _generate_with_fallback(contents: List[Any], config: types.GenerateContentConfig) -> Tuple[Any, str]:
    """
    Invoca Gemini probando secuencialmente la lista de modelos compatibles.
    Si un modelo responde 503 (servidor sobrecargado) o 429, reintenta y pasa al siguiente modelo alternativo.
    """
    last_error = None
    for model_name in CANDIDATE_MODELS:
        for attempt in range(2):
            try:
                logger.info(f"Llamando a Gemini con modelo '{model_name}' (intento {attempt + 1})...")
                response = gemini_client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config
                )
                if response:
                    return response, model_name
            except Exception as e:
                err_str = str(e)
                last_error = e
                logger.warning(f"Excepción con modelo '{model_name}' (intento {attempt + 1}): {err_str[:120]}")

                # Si el modelo no existe o está retirado, saltar inmediatamente al siguiente
                if "404" in err_str or "NOT_FOUND" in err_str:
                    break

                # Si es sobrecarga temporal (503 UNAVAILABLE) o rate limit (429), esperar 1.2s antes de reintentar
                if "503" in err_str or "UNAVAILABLE" in err_str or "429" in err_str:
                    time.sleep(1.2)
                    continue

    raise RuntimeError(
        f"Todos los modelos de Gemini están experimentando alta demanda o fallaron temporalmente. "
        f"Último error: {last_error}"
    )


def _run_gemini_turn(input_contents: List[Any]) -> str:
    """Ejecuta una conversación con Gemini manejando bucles de Tool Calls y tolerancia a fallos."""
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        temperature=0.4,
        tools=[{"function_declarations": AVAILABLE_TOOLS}]
    )

    current_contents = list(input_contents)

    for turn in range(4):
        response, used_model = _generate_with_fallback(current_contents, config)

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
    """Sintetiza texto a audio MP3 con gTTS."""
    clean_text = text.replace("*", "").replace("#", "").replace("`", "").replace("_", "").strip()
    if not clean_text:
        clean_text = "He procesado tu requerimiento correctamente."

    tts = gTTS(text=clean_text[:600], lang="es", slow=False)
    buffer = io.BytesIO()
    tts.write_to_fp(buffer)
    buffer.seek(0)
    buffer.name = "response.mp3"
    return buffer


# =============================================================================
# Handlers de Telegram
# =============================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    welcome_text = (
        "👋 ¡Bienvenido, Cristian! Soy tu **Asistente Decisional y de Gestión Técnica de VilcoSystem**.\n\n"
        "🎙️ **Capacidades habilitadas:**\n"
        "1. **Recepción por Voz:** Dicta las solicitudes de Gerencia o Subgerentes. Las catalogaré automáticamente en prioridad y criticidad.\n"
        "2. **Asesoría de Prioridades:** Pregúntame *'¿Qué debo atender primero y por qué?'* y te daré el análisis ponderado.\n"
        "3. **Bitácora y Queries:** Dicta el avance de tus tareas, consultas SQL ejecutadas o estados.\n"
        "4. **Recepción Documental:** Envíame archivos de Word, Excel, Scripts (.sql, .py) o PDFs para indexarlos a tus pedidos y hacerles preguntas.\n"
        "5. **Reportes:** Pídeme resúmenes ejecutivos por rango de fechas."
    )
    await update.message.reply_text(welcome_text, parse_mode=constants.ParseMode.MARKDOWN)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    voice_obj = message.voice or message.audio
    if not voice_obj:
        return

    logger.info(f"Nota de voz recibida (duración: {voice_obj.duration}s)...")
    await message.reply_chat_action(action=constants.ChatAction.RECORD_VOICE)

    try:
        telegram_file = await context.bot.get_file(voice_obj.file_id)
        audio_bytes = bytes(await telegram_file.download_as_bytearray())

        await message.reply_chat_action(action=constants.ChatAction.TYPING)
        contents = [
            types.Part.from_bytes(data=audio_bytes, mime_type="audio/ogg"),
            "Escucha este audio de Cristian Villa y ejecuta las acciones necesarias (catalogar solicitud, actualizar avance, asesorar prioridad o responder)."
        ]
        ai_response_text = await asyncio.to_thread(_run_gemini_turn, contents)

        # Enviar respuesta de texto
        await message.reply_text(ai_response_text, reply_to_message_id=message.message_id)

        # Enviar audio sintetizado
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
            friendly_msg = "⚠️ Los servidores de Google Gemini están experimentando una saturación temporal de alta demanda (Error 503). Por favor reenvía tu audio en 10-15 segundos."
        elif "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
            friendly_msg = "⚠️ Se ha alcanzado el límite de cuota temporal de la API. Por favor espera unos momentos e intenta de nuevo."
        else:
            friendly_msg = f"⚠️ Ocurrió una incidencia al procesar tu solicitud:\n{err_str[:250]}"

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
            friendly_msg = "⚠️ Los servidores de Google Gemini están experimentando una saturación temporal de alta demanda (Error 503). Por favor reenvía tu mensaje en 10-15 segundos."
        elif "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
            friendly_msg = "⚠️ Límite de cuota temporal alcanzado en la API. Por favor espera unos momentos."
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
    logger.info("Iniciando Asistente Decisional y Documental VilcoSystem (Resiliente a 503)...")
    http_thread = threading.Thread(target=start_health_server, args=(PORT,), daemon=True)
    http_thread.start()

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))

    logger.info("Polling de Telegram iniciado...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
