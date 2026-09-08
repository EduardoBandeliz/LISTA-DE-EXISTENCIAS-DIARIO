"""Funciones opcionales de GPT-6 Astra para el flujo de inventario."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Any

from openai import OpenAI


MODEL = os.getenv("OPENAI_INVENTORY_MODEL", "gpt-6-astra").strip() or "gpt-6-astra"


def enabled() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def _json_response(instructions: str, input_data: Any, name: str, schema: dict) -> dict:
    response = OpenAI().responses.create(
        model=MODEL,
        reasoning={"effort": "low"},
        instructions=instructions,
        input=input_data,
        text={
            "format": {
                "type": "json_schema",
                "name": name,
                "strict": True,
                "schema": schema,
            }
        },
    )
    return json.loads(response.output_text)


def extract_inventory_pdf(pdf_path: Path, list_hint: str = "") -> tuple[str, dict]:
    """Usa Astra solo como respaldo cuando los parsers locales no reconocen el PDF."""
    client = OpenAI()
    with pdf_path.open("rb") as source:
        uploaded = client.files.create(file=source, purpose="user_data")
    try:
        schema = {
            "type": "object",
            "properties": {
                "lista": {"type": "string", "enum": ["M", "G", "PL"]},
                "report_title": {"type": "string"},
                "report_datetime": {"type": "string"},
                "productos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "categoria_pdf": {"type": "string"},
                            "clave_categoria": {"type": "string"},
                            "codigo": {"type": "string"},
                            "nombre": {"type": "string"},
                            "cantidad": {"type": "string"},
                            "precio_lista": {"type": "number"},
                            "precio_publico": {"type": "number"},
                        },
                        "required": [
                            "categoria_pdf", "clave_categoria", "codigo", "nombre",
                            "cantidad", "precio_lista", "precio_publico",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["lista", "report_title", "report_datetime", "productos"],
            "additionalProperties": False,
        }
        result = _json_response(
            (
                "Extrae fielmente este PDF de inventario mayorista. No inventes filas ni valores. "
                "Identifica Lista M, G o PL. Omite existencias cero y precios de 1 peso o menos. "
                "Conserva codigos como texto. En cantidad conserva valores como 5 o 10+. "
                "Para PL usa precio_lista y deja precio_publico en 0 si no existe."
            ),
            [{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": f"Pista proporcionada: {list_hint or 'ninguna'}"},
                    {"type": "input_file", "file_id": uploaded.id},
                ],
            }],
            "inventario_pdf",
            schema,
        )
    finally:
        try:
            client.files.delete(uploaded.id)
        except Exception:
            pass

    rows = []
    list_type = result["lista"]
    for item in result["productos"]:
        quantity_text = str(item["cantidad"]).strip()
        digits = "".join(char for char in quantity_text if char.isdigit())
        quantity = int(digits or 0)
        price = float(item["precio_lista"] or 0)
        if quantity < 1 or price <= 1:
            continue
        row = {
            "id": len(rows) + 1,
            "categoria_pdf": item["categoria_pdf"],
            "clave_categoria": item["clave_categoria"],
            "codigo": item["codigo"].strip(),
            "nombre": item["nombre"].strip(),
            "cantidad": quantity_text,
            "existencia_minima": quantity,
            "disponible": True,
            "precio_lista_m": price,
            "precio_publico": float(item["precio_publico"] or 0),
        }
        if list_type == "PL":
            row["precio_pl"] = price
        rows.append(row)
    inventory = {
        "source_pdf": pdf_path.name,
        "report_title": result["report_title"],
        "report_datetime": result["report_datetime"],
        "lista": list_type,
        "extracted_with": MODEL,
        "total_productos": len(rows),
        "total_disponibles": len(rows),
        "total_agotados": 0,
        "total_omitidos_cero": 0,
        "productos": rows,
    }
    return list_type, inventory


def audit_inventory(payload: dict) -> dict:
    schema = {
        "type": "object",
        "properties": {
            "risk": {"type": "string", "enum": ["low", "medium", "high"]},
            "summary": {"type": "string"},
            "findings": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["risk", "summary", "findings"],
        "additionalProperties": False,
    }
    return _json_response(
        (
            "Eres auditor de inventario mayorista. Evalua exclusivamente los datos recibidos. "
            "No inventes problemas. Marca high solo ante evidencia clara que amerite revision humana. "
            "Responde en espanol breve."
        ),
        json.dumps(payload, ensure_ascii=False),
        "auditoria_inventario",
        schema,
    )


def validate_product_image(image_path: Path, product: dict) -> dict:
    mime = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    schema = {
        "type": "object",
        "properties": {
            "matches": {"type": "boolean"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "detected": {"type": "string"},
            "note": {"type": "string"},
        },
        "required": ["matches", "confidence", "detected", "note"],
        "additionalProperties": False,
    }
    return _json_response(
        (
            "Compara la foto con el producto esperado. Revisa marca, familia/modelo y color visibles. "
            "No rechaces por memoria o region si no pueden verse. matches=false solo con contradiccion clara. "
            "Responde en espanol breve."
        ),
        [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Producto esperado: " + json.dumps(product, ensure_ascii=False)},
                {"type": "input_image", "image_url": f"data:{mime};base64,{encoded}"},
            ],
        }],
        "validacion_imagen",
        schema,
    )


def executive_summary(payload: dict) -> str:
    schema = {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
        "additionalProperties": False,
    }
    result = _json_response(
        (
            "Redacta un reporte ejecutivo de inventario en espanol, maximo 650 caracteres. "
            "Prioriza nuevos, reingresos, cambios importantes, oportunidades y faltantes de imagen. "
            "Usa texto listo para Telegram y no inventes datos."
        ),
        json.dumps(payload, ensure_ascii=False),
        "resumen_ejecutivo",
        schema,
    )
    return result["summary"].strip()


def interpret_inventory_request(text: str, context: dict) -> dict:
    schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["product", "new", "opportunities", "alerts", "missing_images", "links", "status", "help", "unknown"],
            },
            "query": {"type": "string"},
        },
        "required": ["action", "query"],
        "additionalProperties": False,
    }
    return _json_response(
        (
            "Convierte la solicitud del usuario en una accion permitida del bot de inventario. "
            "Para buscar marca o modelo usa product y copia la consulta util en query. "
            "No ejecutes acciones de publicacion, borrado, restauracion o configuracion."
        ),
        json.dumps({"solicitud": text, "contexto": context}, ensure_ascii=False),
        "solicitud_inventario",
        schema,
    )
