#!/usr/bin/env python3
"""
=============================================================================
VilcoSystem - Extractor e Indexador Documental Inteligente
Procesa Word (.docx), Excel (.xlsx), Scripts (.py, .sql, .sh) y PDFs en memoria
=============================================================================
"""

import io
import os
from typing import Tuple

MAX_TEXT_LEN = 40000


def extract_content(file_bytes: bytes, filename: str) -> Tuple[str, str, str]:
    """
    Extrae el contenido textual y genera un metadato/resumen del archivo.
    Retorna: (tipo_detectado, texto_extraido, resumen_breve)
    """
    ext = os.path.splitext(filename)[1].lower()
    
    # 1. Scripts y Archivos de Texto Plano
    code_text_exts = {
        ".sql", ".py", ".sh", ".js", ".ts", ".html", ".css",
        ".json", ".txt", ".csv", ".yaml", ".yml", ".md", ".xml", ".ini", ".env"
    }
    if ext in code_text_exts:
        try:
            text = file_bytes.decode("utf-8", errors="replace")
            resumen = f"Archivo de código/texto '{filename}' ({len(text.splitlines())} líneas, {len(file_bytes)} bytes)."
            return ("SCRIPT/TEXTO", text[:MAX_TEXT_LEN], resumen)
        except Exception as e:
            return ("SCRIPT/TEXTO", f"Error decodificando texto: {e}", "Fallo de decodificación")

    # 2. Hojas de Cálculo Excel (.xlsx, .xls)
    if ext in [".xlsx", ".xlsm", ".xltx"]:
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
            lineas = [f"=== LIBRO EXCEL: {filename} ==="]
            sheet_names = wb.sheetnames
            lineas.append(f"Hojas encontradas ({len(sheet_names)}): {', '.join(sheet_names)}\n")

            for sheet_name in sheet_names[:5]:
                sheet = wb[sheet_name]
                lineas.append(f"--- Hoja: {sheet_name} ---")
                row_count = 0
                for row in sheet.iter_rows(values_only=True):
                    row_vals = [str(cell) if cell is not None else "" for cell in row]
                    if any(row_vals):
                        lineas.append(" | ".join(row_vals[:15]))
                        row_count += 1
                    if row_count >= 50:
                        lineas.append(f"... [Se omiten filas adicionales de la hoja '{sheet_name}']")
                        break
                lineas.append("")

            wb.close()
            full_text = "\n".join(lineas)
            resumen = f"Libro Excel '{filename}' con {len(sheet_names)} hojas ({', '.join(sheet_names[:3])})."
            return ("EXCEL", full_text[:MAX_TEXT_LEN], resumen)
        except Exception as e:
            return ("EXCEL", f"Error al procesar Excel: {e}", f"Excel '{filename}' con error de lectura")

    # 3. Documentos de Word (.docx)
    if ext == ".docx":
        try:
            import docx
            doc = docx.Document(io.BytesIO(file_bytes))
            lineas = [f"=== DOCUMENTO WORD: {filename} ==="]
            
            for p in doc.paragraphs:
                if p.text.strip():
                    lineas.append(p.text.strip())

            if doc.tables:
                lineas.append("\n--- TABLAS DEL DOCUMENTO ---")
                for t_idx, table in enumerate(doc.tables[:5]):
                    lineas.append(f"[Tabla {t_idx + 1}]")
                    for row in table.rows[:30]:
                        lineas.append(" | ".join(cell.text.strip() for cell in row.cells))

            full_text = "\n".join(lineas)
            resumen = f"Documento Word '{filename}' ({len(doc.paragraphs)} párrafos, {len(doc.tables)} tablas)."
            return ("WORD", full_text[:MAX_TEXT_LEN], resumen)
        except Exception as e:
            return ("WORD", f"Error al procesar Word: {e}", f"Word '{filename}' con error de lectura")

    # 4. Archivos PDF (.pdf)
    if ext == ".pdf":
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            lineas = [f"=== DOCUMENTO PDF: {filename} ==="]
            lineas.append(f"Total páginas: {len(reader.pages)}\n")

            for idx, page in enumerate(reader.pages[:20]):
                extracted = page.extract_text()
                if extracted:
                    lineas.append(f"--- Página {idx + 1} ---")
                    lineas.append(extracted.strip())

            full_text = "\n".join(lineas)
            resumen = f"PDF '{filename}' con {len(reader.pages)} páginas."
            return ("PDF", full_text[:MAX_TEXT_LEN], resumen)
        except Exception as e:
            return ("PDF", f"Error al procesar PDF: {e}", f"PDF '{filename}' con error de lectura")

    return ("DESCONOCIDO", f"Archivo binario '{filename}' ({len(file_bytes)} bytes).", f"Archivo adjunto {filename}")
