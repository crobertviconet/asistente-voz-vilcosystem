#!/usr/bin/env python3
"""
=============================================================================
VilcoSystem - Asistente Conversacional de Voz Multimodal (Fase 1)
Líder Técnico: Cristian Villa
Arquitectura: Telegram Bot -> Gemini 2.5 Flash Multimodal (Audio directo) -> gTTS
Soporte Render: Servidor HTTP integrado para Plan Free (Web Service)
=============================================================================
"""

import os
import sys
import io
import asyncio
import logging
import threading
from typing import Optional
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

# Cargar variables de entorno locales si existe un archivo .env
load_dotenv()

# Verificación de variables de entorno
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PORT = int(os.getenv("PORT", "10000"))

# Configuración de Logging Estructurado
logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("VilcoVoiceAssistant")

# Validación temprana de credenciales
if not TELEGRAM_BOT_TOKEN:
    logger.critical("VARIABLE CRÍTICA FALTANTE: 'TELEGRAM_BOT_TOKEN' no está configurada.")
if not GEMINI_API_KEY:
    logger.critical("VARIABLE CRÍTICA FALTANTE: 'GEMINI_API_KEY' no está configurada.")

if not TELEGRAM_BOT_TOKEN or not GEMINI_API_KEY:
    logger.error("Por favor configure las variables de entorno requeridas en Render o en su archivo .env local.")
    sys.exit(1)


# =============================================================================
# Servidor HTTP para Render (Permite usar el Plan Free de Web Service)
# =============================================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    """Manejador HTTP simple para responder a los chequeos de Render."""
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("VilcoSystem Voice Assistant Bot is running OK".encode("utf-8"))

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()

    def log_message(self, format, *args):
        # Silenciar logs excesivos de peticiones HTTP en consola
        pass


def start_health_server(port: int) -> None:
    """Inicia el servidor HTTP en un hilo en segundo plano."""
    try:
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        logger.info(f"Servidor HTTP de salud activo en el puerto {port} para Render.")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Error iniciando servidor HTTP en puerto {port}: {e}")


# =============================================================================
# Importaciones de Telegram y Google GenAI
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

# Inicialización del cliente nativo Google GenAI
try:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    logger.info("Cliente de Google GenAI inicializado correctamente.")
except Exception as e:
    logger.exception(f"Error al inicializar el cliente de Google GenAI: {e}")
    sys.exit(1)

# Instrucción de Sistema optimizada para respuestas de voz y ejecutivas
SYSTEM_INSTRUCTION = (
    "Eres el Asistente Virtual Inteligente de VilcoSystem, diseñado para interactuar "
    "por voz y texto. Responde de forma clara, profesional, concisa y natural, "
    "ideal para ser escuchada en una nota de voz. Evita listas excesivamente largas, "
    "tablas complejas o caracteres markdown extraños que puedan sonar confusos al sintetizarse a audio. "
    "Si el usuario te consulta sobre gestión de tareas, proyectos o información técnica, sé directo y estructurado."
)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manejador del comando /start y bienvenida al usuario."""
    user = update.effective_user
    nombre = user.first_name if user else "estimado usuario"
    
    welcome_text = (
        f"👋 ¡Hola, {nombre}! Bienvenido al **Asistente de Voz de VilcoSystem**.\n\n"
        "🎙️ **¿Cómo interactuar?**\n"
        "• Envíame una **nota de voz** con cualquier consulta, tarea o requerimiento.\n"
        "• También puedes escribirme mensajes de texto tradicionales.\n\n"
        "⚡ *Tu audio es procesado directamente por la red neuronal nativa multimodal de Gemini 2.5 Flash, "
        "sin conversiones intermedias con pérdidas de fidelidad.*"
    )
    await update.message.reply_text(welcome_text, parse_mode=constants.ParseMode.MARKDOWN)
    logger.info(f"Comando /start ejecutado por el usuario {user.id} ({user.username or user.first_name})")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manejador del comando /help."""
    help_text = (
        "ℹ️ **Instrucciones de uso - Asistente VilcoSystem**\n\n"
        "1. **Notas de Voz:** Presiona el botón del micrófono y habla naturalmente. "
        "El asistente responderá con texto y una nota de voz sintetizada.\n"
        "2. **Mensajes de Texto:** Envía preguntas o listas de tareas directamente.\n"
        "3. **Soporte:** Arquitectura basada en Telegram Bot API + Gemini Multimodal."
    )
    await update.message.reply_text(help_text, parse_mode=constants.ParseMode.MARKDOWN)


def _generate_gemini_multimodal_audio(audio_data: bytes, mime_type: str = "audio/ogg") -> str:
    """
    Ejecuta la llamada a la API de Google GenAI enviando el binario directo del audio.
    """
    response = gemini_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            types.Part.from_bytes(data=audio_data, mime_type=mime_type),
            "Por favor, escucha este audio y responde a la consulta del usuario en español."
        ],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0.7,
        )
    )
    return response.text or "No se pudo interpretar el audio o la respuesta fue vacía."


def _generate_gemini_text(prompt: str) -> str:
    """
    Ejecuta la llamada a la API de Google GenAI para mensajes de texto.
    """
    response = gemini_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0.7,
        )
    )
    return response.text or "Respuesta vacía recibida."


def _synthesize_voice(text: str) -> io.BytesIO:
    """
    Sintetiza texto a voz usando gTTS en un buffer de memoria BytesIO.
    Evita escrituras en disco efímero de Render.
    """
    clean_text = text.replace("*", "").replace("#", "").replace("`", "").replace("_", "").strip()
    if not clean_text:
        clean_text = "He recibido tu mensaje correctamente."

    tts = gTTS(text=clean_text, lang="es", slow=False)
    buffer = io.BytesIO()
    tts.write_to_fp(buffer)
    buffer.seek(0)
    buffer.name = "response.ogg"
    return buffer


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Maneja notas de voz recibidas de Telegram:
    1. Descarga el audio en memoria (.ogg / Opus).
    2. Envía el binario directo a Gemini 2.5 Flash (Multimodal nativo).
    3. Sintetiza la respuesta a voz con gTTS.
    4. Devuelve respuesta dual (Texto + Nota de voz).
    """
    message = update.effective_message
    user = update.effective_user
    user_id = user.id if user else "desconocido"

    voice_obj = message.voice or message.audio
    if not voice_obj:
        return

    logger.info(f"Recibida nota de voz de {user_id} (Duración: {voice_obj.duration}s, Tamaño: {voice_obj.file_size} bytes)")
    await message.reply_chat_action(action=constants.ChatAction.RECORD_VOICE)

    try:
        telegram_file = await context.bot.get_file(voice_obj.file_id)
        audio_bytearray = await telegram_file.download_as_bytearray()
        audio_bytes = bytes(audio_bytearray)
        logger.info(f"Audio descargado exitosamente en memoria: {len(audio_bytes)} bytes.")

        await message.reply_chat_action(action=constants.ChatAction.TYPING)
        ai_response_text = await asyncio.to_thread(_generate_gemini_multimodal_audio, audio_bytes, "audio/ogg")
        logger.info("Respuesta de Gemini 2.5 Flash generada satisfactoriamente.")

        await message.reply_chat_action(action=constants.ChatAction.RECORD_VOICE)
        voice_buffer = await asyncio.to_thread(_synthesize_voice, ai_response_text)
        logger.info("Síntesis TTS (gTTS) completada.")

        # Enviar respuesta de texto
        await message.reply_text(
            ai_response_text,
            reply_to_message_id=message.message_id
        )

        # Enviar respuesta de voz
        await message.reply_voice(
            voice=voice_buffer,
            caption="🎙️ *Respuesta de voz (VilcoSystem)*",
            parse_mode=constants.ParseMode.MARKDOWN,
            reply_to_message_id=message.message_id
        )
        logger.info(f"Entrega dual (texto + voz) completada para usuario {user_id}.")

    except Exception as e:
        logger.exception(f"Error procesando nota de voz de {user_id}: {e}")
        await message.reply_text(
            "⚠️ Ocurrió un error al procesar tu nota de voz con el servicio de IA. "
            "Por favor intenta de nuevo en unos momentos.",
            reply_to_message_id=message.message_id
        )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja mensajes de texto tradicionales enviados por el usuario."""
    message = update.effective_message
    user = update.effective_user
    user_id = user.id if user else "desconocido"
    user_text = message.text

    if not user_text:
        return

    logger.info(f"Recibido mensaje de texto de {user_id}: '{user_text[:40]}...'")
    await message.reply_chat_action(action=constants.ChatAction.TYPING)

    try:
        ai_response_text = await asyncio.to_thread(_generate_gemini_text, user_text)

        await message.reply_chat_action(action=constants.ChatAction.RECORD_VOICE)
        voice_buffer = await asyncio.to_thread(_synthesize_voice, ai_response_text)

        await message.reply_text(ai_response_text, reply_to_message_id=message.message_id)

        await message.reply_voice(
            voice=voice_buffer,
            caption="🎙️ *Audio respuesta*",
            parse_mode=constants.ParseMode.MARKDOWN,
            reply_to_message_id=message.message_id
        )
        logger.info(f"Respuesta dual a texto enviada a {user_id}.")

    except Exception as e:
        logger.exception(f"Error procesando mensaje de texto de {user_id}: {e}")
        await message.reply_text(
            "⚠️ Lo siento, ocurrió un problema al procesar tu mensaje con Gemini. Por favor intenta nuevamente.",
            reply_to_message_id=message.message_id
        )


def main() -> None:
    """Punto de entrada principal para el servicio."""
    logger.info("Iniciando Asistente Conversacional de Voz VilcoSystem (Fase 1)...")
    logger.info(f"Versión de Python: {sys.version.split()[0]}")

    # Iniciar servidor HTTP en segundo plano para cumplir con el puerto de Render Free
    http_thread = threading.Thread(target=start_health_server, args=(PORT,), daemon=True)
    http_thread.start()

    # Construcción de la aplicación de Telegram
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Registro de manejadores de comandos
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))

    # Registro de manejadores de mensajes
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))

    logger.info("Polling de Telegram iniciado. Escuchando eventos entrantes...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
