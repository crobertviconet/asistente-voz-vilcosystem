#!/usr/bin/env python3
"""
=============================================================================
VilcoSystem - Motor de Base de Datos Híbrido (SQLite Local + Turso libSQL Cloud)
Manejo de Solicitudes, Jerarquías, Bitácora de Queries y Documentos
Arquitectura: LibSQLConnectionWrapper + LibSQLRow para compatibilidad total
=============================================================================
"""

import os
import sqlite3
import datetime
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("VilcoVoiceAssistant.Database")

DB_FILE = os.getenv("DATABASE_FILE", "tareas_vilcosystem.db")
TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN")


# =============================================================================
# Wrapper de Compatibilidad LibSQLRow para Turso
# =============================================================================
class LibSQLRow(dict):
    """Fila compatible con acceso por nombre de columna, índice numérico y dict(row)."""
    def __init__(self, cursor_description, row_values):
        col_names = [col[0] for col in cursor_description] if cursor_description else []
        self._values = tuple(row_values)
        super().__init__(zip(col_names, row_values))

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


class LibSQLCursorWrapper:
    """Cursor envoltorio para adaptar tuplas crudas de libSQL a LibSQLRow."""
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, sql, params=()):
        self._cursor.execute(sql, params)
        return self

    def executemany(self, sql, seq_of_params):
        self._cursor.executemany(sql, seq_of_params)
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        if isinstance(row, (dict, LibSQLRow)):
            return row
        return LibSQLRow(self._cursor.description, row)

    def fetchall(self):
        rows = self._cursor.fetchall()
        if not rows:
            return []
        if isinstance(rows[0], (dict, LibSQLRow)):
            return rows
        desc = self._cursor.description
        return [LibSQLRow(desc, r) for r in rows]

    def fetchmany(self, size=None):
        rows = self._cursor.fetchmany(size) if size else self._cursor.fetchmany()
        if not rows:
            return []
        desc = self._cursor.description
        return [LibSQLRow(desc, r) for r in rows]

    @property
    def lastrowid(self):
        return getattr(self._cursor, 'lastrowid', None)

    @property
    def rowcount(self):
        return getattr(self._cursor, 'rowcount', -1)

    @property
    def description(self):
        return self._cursor.description

    def close(self):
        return self._cursor.close()

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                break
            yield row


class LibSQLConnectionWrapper:
    """Envoltorio de conexión a Turso para gestionar cursores compatibles y contexto."""
    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return LibSQLCursorWrapper(self._conn.cursor())

    def execute(self, sql, params=()):
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def executemany(self, sql, seq_of_params):
        cur = self.cursor()
        cur.executemany(sql, seq_of_params)
        return cur

    def executescript(self, script):
        for stmt in script.split(';'):
            stmt_clean = stmt.strip()
            if stmt_clean:
                self.execute(stmt_clean)
        return self

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()


# =============================================================================
# Gestor de Conexiones Híbridas
# =============================================================================
def get_db_connection():
    """
    Retorna conexión activa:
    1. Si TURSO_DATABASE_URL y TURSO_AUTH_TOKEN existen: Conecta a Turso Cloud.
    2. Si fallan o no existen: Fallback defensivo a SQLite Local (tareas_vilcosystem.db).
    """
    if TURSO_DATABASE_URL and TURSO_AUTH_TOKEN:
        try:
            import libsql
            db_url = TURSO_DATABASE_URL.strip()
            if db_url.lower().startswith('libsql://'):
                db_url = 'libsql://' + db_url[9:]
            
            auth_token = TURSO_AUTH_TOKEN.strip()
            if auth_token.startswith('EyJ'):
                auth_token = 'eyJ' + auth_token[3:]

            raw_conn = libsql.connect(
                database=db_url,
                auth_token=auth_token
            )
            return LibSQLConnectionWrapper(raw_conn)
        except Exception as e:
            logger.warning(f"Error conectando a Turso Cloud ({e}). Fallback a SQLite local.")

    # Conexión local estándar SQLite
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def init_db() -> None:
    """Inicializa las tablas relacionales de la base de datos de forma idempotente."""
    try:
        with get_db_connection() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS solicitudes_tareas (
                id TEXT PRIMARY KEY,
                titulo TEXT NOT NULL,
                solicitante TEXT NOT NULL,
                cargo_solicitante TEXT NOT NULL,
                area TEXT NOT NULL,
                descripcion TEXT NOT NULL,
                prioridad TEXT CHECK(prioridad IN ('URGENTE', 'ALTA', 'MEDIA', 'BAJA')),
                criticidad TEXT CHECK(criticidad IN ('CRITICA', 'ALTA', 'MODERADA', 'LEVE')),
                justificacion_ia TEXT,
                estado TEXT CHECK(estado IN ('PENDIENTE', 'EN_PROCESO', 'BLOQUEADO', 'REVISION', 'COMPLETADO')) DEFAULT 'PENDIENTE',
                porcentaje_avance INTEGER DEFAULT 0,
                fecha_limite TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS bitacora_avance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tarea_id TEXT NOT NULL,
                comentario TEXT NOT NULL,
                query_sql TEXT,
                doc_referencia TEXT,
                porcentaje INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (tarea_id) REFERENCES solicitudes_tareas(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS documentos_tareas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tarea_id TEXT NOT NULL,
                nombre_archivo TEXT NOT NULL,
                tipo_archivo TEXT NOT NULL,
                tamano_bytes INTEGER,
                texto_extraido TEXT NOT NULL,
                resumen TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (tarea_id) REFERENCES solicitudes_tareas(id) ON DELETE CASCADE
            );
            """)
        logger.info("Base de datos inicializada correctamente.")
    except Exception as e:
        logger.exception(f"Error inicializando base de datos: {e}")


def generar_siguiente_id() -> str:
    """Genera un código correlativo legible como REQ-001, REQ-002."""
    with get_db_connection() as conn:
        cursor = conn.execute("SELECT id FROM solicitudes_tareas ORDER BY ROWID DESC LIMIT 1;")
        row = cursor.fetchone()
        if not row:
            return "REQ-001"
        try:
            ultimo_num = int(row["id"].replace("REQ-", ""))
            return f"REQ-{ultimo_num + 1:03d}"
        except Exception:
            count = conn.execute("SELECT COUNT(*) as c FROM solicitudes_tareas;").fetchone()["c"]
            return f"REQ-{count + 1:03d}"


def crear_solicitud(
    titulo: str,
    solicitante: str,
    cargo_solicitante: str,
    area: str,
    descripcion: str,
    prioridad: str = "MEDIA",
    criticidad: str = "MODERADA",
    justificacion_ia: str = "",
    fecha_limite: Optional[str] = None
) -> Dict[str, Any]:
    """Registra una nueva solicitud y retorna el registro creado."""
    req_id = generar_siguiente_id()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    prio_norm = prioridad.upper() if prioridad.upper() in ['URGENTE', 'ALTA', 'MEDIA', 'BAJA'] else 'MEDIA'
    crit_norm = criticidad.upper() if criticidad.upper() in ['CRITICA', 'ALTA', 'MODERADA', 'LEVE'] else 'MODERADA'

    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO solicitudes_tareas (
                id, titulo, solicitante, cargo_solicitante, area,
                descripcion, prioridad, criticidad, justificacion_ia,
                estado, porcentaje_avance, fecha_limite, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDIENTE', 0, ?, ?)
        """, (
            req_id, titulo, solicitante, cargo_solicitante, area,
            descripcion, prio_norm, crit_norm, justificacion_ia,
            fecha_limite, now_str
        ))
        
        conn.execute("""
            INSERT INTO bitacora_avance (tarea_id, comentario, porcentaje, created_at)
            VALUES (?, ?, 0, ?)
        """, (req_id, f"Solicitud registrada por {solicitante} ({cargo_solicitante} - {area}).", now_str))

    return obtener_tarea_por_id(req_id)


def actualizar_avance(
    tarea_id: str,
    comentario: str,
    nuevo_estado: Optional[str] = None,
    porcentaje: Optional[int] = None,
    query_sql: Optional[str] = None,
    doc_referencia: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Registra un evento de avance en la bitácora y actualiza el estado de la tarea."""
    tarea = obtener_tarea_por_id(tarea_id)
    if not tarea:
        tarea = buscar_tarea_por_termino(tarea_id)
        if not tarea:
            return None
        tarea_id = tarea["id"]

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    update_fields = []
    update_params = []

    if nuevo_estado:
        estado_norm = nuevo_estado.upper()
        if estado_norm in ['PENDIENTE', 'EN_PROCESO', 'BLOQUEADO', 'REVISION', 'COMPLETADO']:
            update_fields.append("estado = ?")
            update_params.append(estado_norm)
            if estado_norm == "COMPLETADO":
                update_fields.append("completed_at = ?")
                update_params.append(now_str)
                if porcentaje is None:
                    porcentaje = 100

    if porcentaje is not None:
        p_val = max(0, min(100, int(porcentaje)))
        update_fields.append("porcentaje_avance = ?")
        update_params.append(p_val)

    with get_db_connection() as conn:
        if update_fields:
            update_params.append(tarea_id)
            sql = f"UPDATE solicitudes_tareas SET {', '.join(update_fields)} WHERE id = ?;"
            conn.execute(sql, tuple(update_params))

        conn.execute("""
            INSERT INTO bitacora_avance (tarea_id, comentario, query_sql, doc_referencia, porcentaje, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            tarea_id, comentario, query_sql, doc_referencia,
            porcentaje if porcentaje is not None else tarea.get("porcentaje_avance", 0),
            now_str
        ))

    return obtener_tarea_por_id(tarea_id)


def registrar_documento(
    tarea_id: str,
    nombre_archivo: str,
    tipo_archivo: str,
    tamano_bytes: int,
    texto_extraido: str,
    resumen: str = ""
) -> int:
    """Registra un documento indexado y su contenido de texto para la tarea."""
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection() as conn:
        cursor = conn.execute("""
            INSERT INTO documentos_tareas (
                tarea_id, nombre_archivo, tipo_archivo, tamano_bytes, texto_extraido, resumen, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (tarea_id, nombre_archivo, tipo_archivo, tamano_bytes, texto_extraido, resumen, now_str))
        doc_id = cursor.lastrowid

        conn.execute("""
            INSERT INTO bitacora_avance (tarea_id, comentario, doc_referencia, created_at)
            VALUES (?, ?, ?, ?)
        """, (tarea_id, f"Documento adjunto indexado: {nombre_archivo} ({tipo_archivo})", nombre_archivo, now_str))

        return doc_id


def obtener_documentos_de_tarea(tarea_id: str) -> List[Dict[str, Any]]:
    """Retorna todos los documentos asociados a una tarea."""
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM documentos_tareas WHERE tarea_id = ? ORDER BY id DESC;",
            (tarea_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def obtener_tarea_por_id(tarea_id: str) -> Optional[Dict[str, Any]]:
    """Obtiene una tarea por su código exacto."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM solicitudes_tareas WHERE id = ?;", (tarea_id,)).fetchone()
        if not row:
            return None
        t = dict(row)
        b_rows = conn.execute(
            "SELECT * FROM bitacora_avance WHERE tarea_id = ? ORDER BY id ASC;",
            (tarea_id,)
        ).fetchall()
        t["bitacora"] = [dict(b) for b in b_rows]
        d_rows = conn.execute(
            "SELECT id, nombre_archivo, tipo_archivo, tamano_bytes, resumen, created_at FROM documentos_tareas WHERE tarea_id = ?;",
            (tarea_id,)
        ).fetchall()
        t["documentos"] = [dict(d) for d in d_rows]
        return t


def buscar_tarea_por_termino(termino: str) -> Optional[Dict[str, Any]]:
    """Busca una tarea por término en título, solicitante o descripción."""
    term_like = f"%{termino.strip()}%"
    with get_db_connection() as conn:
        row = conn.execute("""
            SELECT * FROM solicitudes_tareas 
            WHERE id LIKE ? OR titulo LIKE ? OR solicitante LIKE ? OR area LIKE ?
            ORDER BY ROWID DESC LIMIT 1;
        """, (term_like, term_like, term_like, term_like)).fetchone()
        if row:
            return obtener_tarea_por_id(row["id"])
    return None


def obtener_tareas_pendientes() -> List[Dict[str, Any]]:
    """Retorna todas las tareas en curso o pendientes ordenadas por impacto y jerarquía."""
    with get_db_connection() as conn:
        rows = conn.execute("""
            SELECT * FROM solicitudes_tareas 
            WHERE estado IN ('PENDIENTE', 'EN_PROCESO', 'BLOQUEADO', 'REVISION')
            ORDER BY 
                CASE prioridad
                    WHEN 'URGENTE' THEN 1
                    WHEN 'ALTA' THEN 2
                    WHEN 'MEDIA' THEN 3
                    WHEN 'BAJA' THEN 4
                    ELSE 5
                END ASC,
                CASE criticidad
                    WHEN 'CRITICA' THEN 1
                    WHEN 'ALTA' THEN 2
                    WHEN 'MODERADA' THEN 3
                    WHEN 'LEVE' THEN 4
                    ELSE 5
                END ASC,
                created_at ASC;
        """).fetchall()
        return [dict(r) for r in rows]


def obtener_ultima_tarea() -> Optional[Dict[str, Any]]:
    """Retorna la última tarea registrada o modificada recientemente."""
    with get_db_connection() as conn:
        row = conn.execute("""
            SELECT * FROM solicitudes_tareas 
            ORDER BY ROWID DESC LIMIT 1;
        """).fetchone()
        if row:
            return obtener_tarea_por_id(row["id"])
    return None


def obtener_reporte_por_fechas(fecha_inicio: str, fecha_fin: str, estado: Optional[str] = None) -> List[Dict[str, Any]]:
    """Genera un reporte de tareas creadas o completadas en un rango de fechas."""
    with get_db_connection() as conn:
        query = """
            SELECT * FROM solicitudes_tareas
            WHERE date(created_at) >= date(?) AND date(created_at) <= date(?)
        """
        params = [fecha_inicio, fecha_fin]
        if estado:
            query += " AND estado = ?"
            params.append(estado.upper())
        query += " ORDER BY created_at DESC;"

        rows = conn.execute(query, tuple(params)).fetchall()
        resultado = []
        for r in rows:
            t = dict(r)
            d_count = conn.execute(
                "SELECT COUNT(*) as c FROM documentos_tareas WHERE tarea_id = ?;",
                (t["id"],)
            ).fetchone()["c"]
            t["total_documentos"] = d_count
            resultado.append(t)
        return resultado


init_db()
