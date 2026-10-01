#!/usr/bin/env python3
"""
=============================================================================
VilcoSystem - Asistente Conversacional de Voz Multimodal (Fase 1 - Resiliente)
Líder Técnico: Cristian Villa
Arquitectura: Telegram Bot -> Gemini Multimodal (Fallback 2.5/2.0/1.5) -> gTTS
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

# Cargar variables de entorno
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PORT = int(os.getenv("PORT", "10000"))

logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("VilcoVoiceAssistant")

if not TELEGRAM_BOT_TOKEN:
    logger.critical("VARIABLE CRÍTICA FALTANTE: 'TELEGRAM_BOT_TOKEN' no configurada.")
if not GEMINI_API_KEY:
    logger.critical("VARIABLE CRÍTICA FALTANTE: 'GEMINI_API_KEY' no configurada.")

if not TELEGRAM_BOT_TOKEN or not GEMINI_API_KEY:
    sys.exit(1)


# =============================================================================
# Servidor HTTP para Render Free Plan
# =============================================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
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
    logger.info("Cliente de Google GenAI inicializado correctamente.")
except Exception as e:
    logger.exception(f"Error inicializando cliente de Google GenAI: {e}")
    sys.exit(1)

SYSTEM_INSTRUCTION = (
    "Eres el Asistente Virtual Inteligente de VilcoSystem. "
    "Responde de forma clara, profesional, concisa y conversacional en español. "
    "Evita listas excesivamente largas, tablas o formatos complejos que dificulten la escucha en audio."
)

# Lista de modelos compatibles en orden de preferencia
CANDIDATE_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]


def _call_gemini_multimodal(audio_bytes: bytes) -> str:
    """Invoca Gemini probando modelos compatibles si uno arroja 404 o no está disponible."""
    last_error = None
    for model_name in CANDIDATE_MODELS:
        try:
            logger.info(f"Intentando procesar audio con modelo: '{model_name}'...")
            response = gemini_client.models.generate_content(
                model=model_name,
                contents=[
                    types.Part.from_bytes(data=audio_bytes, mime_type="audio/ogg"),
                    "Por favor, escucha atentamente este audio y responde a la consulta del usuario en español."
                ],
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    temperature=0.7,
                )
            )
            if response and response.text:
                logger.info(f"Éxito con modelo '{model_name}'.")
                return response.text
        except Exception as e:
            logger.warning(f"Falla con modelo '{model_name}': {e}")
            last_error = e

    raise RuntimeError(f"No se pudo procesar el audio con ningún modelo disponible. Último error: {last_error}")


def _call_gemini_text(prompt: str) -> str:
    """Invoca Gemini para texto con fallback entre modelos."""
    last_error = None
    for model_name in CANDIDATE_MODELS:
        try:
            response = gemini_client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    temperature=0.7,
                )
            )
            if response and response.text:
                return response.text
        except Exception as e:
            last_error = e

    raise RuntimeError(f"Falla procesando texto. Último error: {last_error}")


def _synthesize_voice(text: str) -> io.BytesIO:
    """Sintetiza texto a audio MP3 con gTTS."""
    clean_text = text.replace("*", "").replace("#", "").replace("`", "").replace("_", "").strip()
    if not clean_text:
        clean_text = "He recibido tu mensaje correctamente."

    tts = gTTS(text=clean_text, lang="es", slow=False)
    buffer = io.BytesIO()
    tts.write_to_fp(buffer)
    buffer.seek(0)
    buffer.name = "response.mp3"
    return buffer


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    welcome_text = (
        "👋 ¡Hola! Bienvenido al **Asistente de Voz de VilcoSystem**.\n\n"
        "🎙️ Envíame una **nota de voz** o escribe un mensaje y te responderé de inmediato."
    )
    await update.message.reply_text(welcome_text, parse_mode=constants.ParseMode.MARKDOWN)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user_id = update.effective_user.id if update.effective_user else "unknown"
    voice_obj = message.voice or message.audio

    if not voice_obj:
        return

    logger.info(f"Procesando audio de usuario {user_id} (id={voice_obj.file_id})...")
    await message.reply_chat_action(action=constants.ChatAction.RECORD_VOICE)

    # Paso 1: Descargar archivo de Telegram
    try:
        telegram_file = await context.bot.get_file(voice_obj.file_id)
        audio_bytearray = await telegram_file.download_as_bytearray()
        audio_bytes = bytes(audio_bytearray)
        logger.info(f"Audio descargado: {len(audio_bytes)} bytes.")
    except Exception as e:
        logger.exception(f"Error descargando audio de Telegram: {e}")
        await message.reply_text(f"⚠️ Error al descargar el audio de Telegram: {e}", reply_to_message_id=message.message_id)
        return

    # Paso 2: Inferencia en Gemini (multimodal)
    await message.reply_chat_action(action=constants.ChatAction.TYPING)
    try:
        ai_response_text = await asyncio.to_thread(_call_gemini_multimodal, audio_bytes)
        logger.info("Respuesta de Gemini obtenida con éxito.")
    except Exception as e:
        logger.exception(f"Error al invocar API de Gemini: {e}")
        # Notificar causa específica del error para facilitar diagnóstico inmediato
        err_msg = str(e)
        if "403" in err_msg or "API_KEY_INVALID" in err_msg:
            detalle = "Tu GEMINI_API_KEY no es válida o fue revocada. Revisa Google AI Studio."
        elif "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
            detalle = "Límite de cuota alcanzado en la API de Gemini. Espera unos minutos."
        elif "404" in err_msg:
            detalle = "Modelo no disponible en tu región o cuenta."
        else:
            detalle = f"Detalle técnico: {err_msg[:120]}"

        await message.reply_text(
            f"⚠️ Error con el servicio de IA:\n{detalle}",
            reply_to_message_id=message.message_id
        )
        return

    # Paso 3: Enviar respuesta de texto PRIMERO (garantiza entrega de la respuesta)
    try:
        await message.reply_text(ai_response_text, reply_to_message_id=message.message_id)
    except Exception as e:
        logger.error(f"Error enviando mensaje de texto: {e}")

    # Paso 4: Síntesis de voz (desacoplada para no invalidar el texto si gTTS falla)
    try:
        await message.reply_chat_action(action=constants.ChatAction.RECORD_VOICE)
        voice_buffer = await asyncio.to_thread(_synthesize_voice, ai_response_text)
        
        # Enviar como nota de voz; si Telegram rechaza el formato, intentar como audio
        try:
            await message.reply_voice(
                voice=voice_buffer,
                caption="🎙️ *Respuesta de voz*",
                parse_mode=constants.ParseMode.MARKDOWN,
                reply_to_message_id=message.message_id
            )
        except Exception:
            voice_buffer.seek(0)
            await message.reply_audio(
                audio=voice_buffer,
                title="Respuesta de Voz VilcoSystem",
                reply_to_message_id=message.message_id
            )
        logger.info("Audio respuesta enviado exitosamente.")
    except Exception as e:
        logger.warning(f"No se pudo generar la nota de voz TTS, pero el texto fue entregado: {e}")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user_text = message.text
    if not user_text:
        return

    await message.reply_chat_action(action=constants.ChatAction.TYPING)
    try:
        ai_response_text = await asyncio.to_thread(_call_gemini_text, user_text)
        await message.reply_text(ai_response_text, reply_to_message_id=message.message_id)
    except Exception as e:
        logger.exception(f"Error procesando texto: {e}")
        await message.reply_text(f"⚠️ Error procesando mensaje: {e}", reply_to_message_id=message.message_id)


def main() -> None:
    logger.info("Iniciando Asistente de Voz VilcoSystem (Versión Resiliente)...")
    http_thread = threading.Thread(target=start_health_server, args=(PORT,), daemon=True)
    http_thread.start()

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))

    logger.info("Polling iniciado...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
