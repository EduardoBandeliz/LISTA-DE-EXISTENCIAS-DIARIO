#!/usr/bin/env python3
"""Convierte la lista Excel binaria de Payjoy/Payphone al JSON del catalogo."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

from pyxlsb import open_workbook


def _text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _stable_code(name: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]+", " ", name.upper()).strip()
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12].upper()
    return f"PAY-{digest}"


def extract_payjoy_excel(excel_path: Path) -> dict:
    rows: list[dict] = []
    header_found = False
    with open_workbook(str(excel_path)) as workbook:
        if not workbook.sheets:
            raise ValueError("El Excel no contiene hojas.")
        sheet_name = workbook.sheets[0]
        with workbook.get_sheet(sheet_name) as sheet:
            for raw_row in sheet.rows():
                values = [cell.v for cell in raw_row]
                first = _text(values[0] if values else "")
                second = values[1] if len(values) > 1 else None
                if not header_found:
                    if first.upper() == "PRODUCTO" and _text(second).upper() == "COSTO":
                        header_found = True
                    continue
                if not first:
                    continue
                try:
                    price = round(float(second), 2)
                except (TypeError, ValueError):
                    raise ValueError(f"Costo invalido para {first}: {second!r}")
                if price < 0:
                    raise ValueError(f"Costo negativo para {first}: {price}")
                rows.append(
                    {
                        "id": len(rows) + 1,
                        "codigo": _stable_code(first),
                        "nombre": first,
                        "precio_lista_m": price,
                        "precio_payjoy": price,
                        "disponible": True,
                        "lista": "PAYJOY",
                        "categoria_pdf": "EQUIPOS PAYJOY - PAYPHONE",
                    }
                )

    if not header_found:
        raise ValueError("No encontre las columnas PRODUCTO y COSTO.")
    if not rows:
        raise ValueError("El Excel no contiene productos validos.")
    return {
        "source_excel": excel_path.name,
        "report_datetime": datetime.now().isoformat(timespec="seconds"),
        "lista": "PAYJOY",
        "nombre_lista": "Equipos Payjoy - Payphone",
        "total_productos": len(rows),
        "total_disponibles": len(rows),
        "total_agotados": 0,
        "productos": rows,
    }
