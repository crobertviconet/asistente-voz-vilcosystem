# Asistente Conversacional de Voz VilcoSystem (Fase 1)

**Líder Técnico:** Cristian Villa  
**Organización:** VilcoSystem  
**Arquitectura:** Python 3.11+ | `python-telegram-bot` v21+ | Google GenAI SDK (`gemini-2.5-flash`) | `gTTS`

---

## 1. Resumen de la Solución (Fase 1)

El bot resuelve de raíz los problemas de transcripción local (ffmpeg/STT offline) en dispositivos móviles implementando un **flujo multimodal nativo en la nube**:

```
[Usuario Telegram] 
       │  (Nota de voz .oga / Opus)
       ▼
[Telegram Bot API] 
       │  (Descarga directa en memoria: bytes)
       ▼
[assistant_bot.py] 
       │  (Payload binario con mime_type="audio/ogg")
       ▼
[Gemini 2.5 Flash API] (Interpretación acústica directa sin pérdida)
       │  (Respuesta contextual en texto)
       ▼
[gTTS Engine] (Síntesis de voz en memoria BytesIO)
       │
       ├─► Telegram reply_text (Transcripción / respuesta escrita)
       └─► Telegram reply_voice (Nota de voz sintetizada)
```

---

## 2. Estructura de Archivos

```
AsistenteTareas/
├── assistant_bot.py     # Servidor HTTP web dual + Bot de Telegram asíncrono
├── portal_tareas.html   # Portal Web Ejecutivo y Operativo (Dashboard y Bitácora)
├── task_database.py     # Motor de base de datos híbrido (Turso Cloud + SQLite local)
├── document_parser.py   # Indexador y extractor documental (Word, Excel, PDF, Scripts)
├── requirements.txt     # Dependencias de Python
├── Procfile             # Definición de proceso web para Render / PaaS
├── render.yaml          # Blueprint de Infraestructura como Código (IaC) para Render
├── .env.example         # Plantilla de variables de entorno
└── README.md            # Documentación técnica completa y manual de uso
```

---

## 3. Variables de Entorno Requeridas

| Variable | Descripción | Ejemplo / Origen |
| :--- | :--- | :--- |
| `TELEGRAM_BOT_TOKEN` | Token de autenticación del Bot | Obtenido de `@BotFather` en Telegram |
| `GEMINI_API_KEY` | Llave de API de Google GenAI | Obtenida de Google AI Studio |
| `PYTHONUNBUFFERED` | Forzar salida inmediata de logs sin buffer | `1` (recomendado en producción) |

---

## 4. Guía de Despliegue en Render (Paso a Paso)

### Opción A: Despliegue con Blueprint (`render.yaml`) - *Recomendado*
1. Sube este repositorio a tu GitHub/GitLab (e.g. `VilcoSystem/asistente-tareas`).
2. En [Render Dashboard](https://dashboard.render.com/), haz clic en **New +** > **Blueprint**.
3. Conecta el repositorio. Render detectará automáticamente `render.yaml`.
4. En la pantalla de configuración, Render te solicitará completar los valores de `TELEGRAM_BOT_TOKEN` y `GEMINI_API_KEY`.
5. Haz clic en **Apply**. Render creará un **Background Worker** gratuito y desplegará el bot.

### Opción B: Despliegue Manual como Background Worker
1. En Render, selecciona **New +** > **Background Worker**.
2. Conecta tu repositorio de GitHub.
3. Configura los siguientes campos:
   - **Name:** `vilcosystem-voice-assistant`
   - **Environment:** `Python 3`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python assistant_bot.py`
   - **Plan:** `Free` (o `Starter` para 100% SLA)
4. En la sección **Environment Variables**, añade:
   - `TELEGRAM_BOT_TOKEN`: Tu token de Telegram.
   - `GEMINI_API_KEY`: Tu API Key de Google GenAI.
   - `PYTHONUNBUFFERED`: `1`
5. Haz clic en **Create Background Worker**.

> **Nota Crítica sobre Workers vs Web Services:**  
> Un bot que usa `run_polling()` debe correr como un **Worker**, no como un Web Service con puerto HTTP, ya que no escucha peticiones HTTP entrantes. Si se configurara como Web Service sin exponer un servidor web (como FastAPI/Uvicorn), Render marcaría timeout al no detectar un puerto HTTP abierto.

---

## 5. Monitoreo y Validación de Logs

En el panel de Render, abre la pestaña **Logs** del worker. Debes observar la siguiente secuencia de eventos:

```text
2026-10-01 04:30:15 - [INFO] - VilcoVoiceAssistant - Iniciando Asistente Conversacional de Voz VilcoSystem (Fase 1)...
2026-10-01 04:30:16 - [INFO] - VilcoVoiceAssistant - Cliente de Google GenAI inicializado correctamente.
2026-10-01 04:30:17 - [INFO] - VilcoVoiceAssistant - Polling de Telegram iniciado. Escuchando eventos entrantes...
```

Al enviar una nota de voz desde Telegram:
```text
2026-10-01 04:31:02 - [INFO] - VilcoVoiceAssistant - Recibida nota de voz de 12345678 (Duración: 5s, Tamaño: 12450 bytes)
2026-10-01 04:31:03 - [INFO] - VilcoVoiceAssistant - Audio descargado exitosamente en memoria: 12450 bytes.
2026-10-01 04:31:04 - [INFO] - VilcoVoiceAssistant - Respuesta de Gemini 2.5 Flash generada satisfactoriamente.
2026-10-01 04:31:05 - [INFO] - VilcoVoiceAssistant - Síntesis TTS (gTTS) completada.
2026-10-01 04:31:06 - [INFO] - VilcoVoiceAssistant - Entrega dual (texto + voz) completada para usuario 12345678.
```

---

## 6. Pruebas Locales (Desarrollo)

Para probar el bot localmente en tu estación de trabajo:

```bash
cd AsistenteTareas/
cp .env.example .env
# Edita .env con tus credenciales reales
python3 -m venv venv
source venv/bin/activate  # En Windows: venv\Scripts\activate
pip install -r requirements.txt
python assistant_bot.py
```
Abre tu bot en Telegram y envía una nota de voz.


---

## 7. Portal Web y Dashboard de Control en Tiempo Real

El Asistente incorpora un **Portal Web Ejecutivo y Operativo** servido directamente en el puerto HTTP de Render (`PORT`, por defecto `10000`):

- **Acceso en Producción:** `https://tu-servicio.onrender.com/`
- **Acceso Local:** `http://localhost:10000/` o abriendo directamente `portal_tareas.html` en el navegador.

### Características Principales del Portal:
1. **Indicadores en un Golpe de Vista (KPI Cards):**
   - **Total de Solicitudes:** Conteo consolidado de requerimientos históricos y activos.
   - **Tareas Realizadas:** Conteo y tasa porcentual de cumplimiento sobre el total.
   - **Tareas en Curso:** Conteo activo y promedio de avance porcentual ponderado.
   - **Atención Urgente / Crítica:** Alertas visuales destacadas para tareas de impacto mayor (recaudación, cortes, balance energético).
   - **Avance Global del Equipo:** Barra de progreso operacional consolidada.

2. **Analítica Visual Interactiva (Chart.js):**
   - **Distribución por Estado:** Gráfico dona interactivo (Completado, En Proceso, Pendiente, Revisión, Bloqueado).
   - **Carga de Trabajo por Área:** Gráfico de barras por departamento (Operaciones, Facturación, TI, Finanzas, Gerencia).
   - **Matriz de Prioridad y Criticidad:** Visualización de criticidad y urgencia.

3. **Vistas Conmutables:**
   - **Vista Tabla Ejecutiva:** Listado tabular con buscador en vivo, filtros por área, estado y prioridad, badges jerárquicos y barras de progreso.
   - **Vista Cuadrícula de Tarjetas:** Formato visual tipo tablero para revisión rápida de estados.

4. **Detalle Integral y Bitácora Técnica:**
   - Modal con justificación decisional generada por la IA (impacto de negocio).
   - Documentos técnicos adjuntos (Word, Excel, PDF, Scripts) con vista previa de resúmenes.
   - Línea de tiempo cronológica con hitos, comentarios, porcentajes de avance y queries SQL ejecutadas (con botón para copiar código).

5. **Operatividad Web y Exportación:**
   - Registro y actualización de avances directamente desde la web (actualiza Turso/SQLite en tiempo real).
   - Registro manual de nuevos requerimientos.
   - Exportación de reportes en formato CSV compatible con Microsoft Excel (UTF-8 BOM).
   - Formato optimizado para impresión o guardado en PDF (`Ctrl + P`).
