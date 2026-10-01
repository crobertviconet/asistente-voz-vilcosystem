#!/usr/bin/env python3
"""
=============================================================================
VilcoSystem - Asistente Conversacional de Voz Multimodal (Fase 1)
Líder Técnico: Cristian Villa
Arquitectura: Telegram Bot -> Gemini 2.5 Flash Multimodal (Audio directo) -> gTTS
=============================================================================
"""

import os
import sys
import io
import asyncio
import logging
from typing import Optional
from dotenv import load_dotenv

# Cargar variables de entorno locales si existe un archivo .env
load_dotenv()

# Verificación defensiva de variables de entorno antes de importar librerías pesadas
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Configuración de Logging Estructurado para Render / Cloud Logs
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

# Importaciones de Telegram y Google GenAI
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
    Ejecuta la llamada bloqueante a la API de Google GenAI enviando el binario directo del audio.
    Se ejecuta en un hilo secundario mediante asyncio.to_thread para no bloquear el loop asíncrono.
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
    # Limpieza simple de markdown para que el TTS no lea asteriscos o almohadillas
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

    # Determinar si es nota de voz o archivo de audio
    voice_obj = message.voice or message.audio
    if not voice_obj:
        return

    logger.info(f"Recibida nota de voz de {user_id} (Duración: {voice_obj.duration}s, Tamaño: {voice_obj.file_size} bytes)")

    # Indicar al usuario que el bot está procesando y escuchando
    await message.reply_chat_action(action=constants.ChatAction.RECORD_VOICE)

    try:
        # Descargar el archivo de Telegram a memoria
        telegram_file = await context.bot.get_file(voice_obj.file_id)
        audio_bytearray = await telegram_file.download_as_bytearray()
        audio_bytes = bytes(audio_bytearray)
        logger.info(f"Audio descargado exitosamente en memoria: {len(audio_bytes)} bytes.")

        # Inferencia Multimodal en Gemini (ejecutado en subproceso para no congelar el loop)
        await message.reply_chat_action(action=constants.ChatAction.TYPING)
        ai_response_text = await asyncio.to_thread(_generate_gemini_multimodal_audio, audio_bytes, "audio/ogg")
        logger.info("Respuesta de Gemini 2.5 Flash generada satisfactoriamente.")

        # Síntesis TTS asíncrona a buffer en memoria
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
        # Consulta de texto a Gemini 2.5 Flash
        ai_response_text = await asyncio.to_thread(_generate_gemini_text, user_text)

        # Generar también el audio para mantener la experiencia de voz
        await message.reply_chat_action(action=constants.ChatAction.RECORD_VOICE)
        voice_buffer = await asyncio.to_thread(_synthesize_voice, ai_response_text)

        # Enviar texto
        await message.reply_text(ai_response_text, reply_to_message_id=message.message_id)

        # Enviar voz complementaria
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
    """Punto de entrada principal para el servicio Worker."""
    logger.info("Iniciando Asistente Conversacional de Voz VilcoSystem (Fase 1)...")
    logger.info(f"Versión de Python: {sys.version.split()[0]}")

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
