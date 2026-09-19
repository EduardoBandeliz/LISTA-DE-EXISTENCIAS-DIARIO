#!/usr/bin/env python3
import hashlib
import json
import re
import subprocess
from copy import deepcopy
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OCR_SCRIPT = ROOT / "vision_catalog_ocr.swift"
PRICE_RE = re.compile(r"^\$\s*([\d,.]+)$")
IGNORED_WORDS = {
    "NACIONAL", "LATINO", "ASIAN", "USA", "ESIM", "PJ", "PRE", "GRADO",
    "AZUL", "NEGRO", "BLANCO", "GRIS", "VERDE", "VIOLETA", "MORADO", "ROSA",
    "DORADO", "PLATA", "NARANJA", "ROJO", "AMARILLO", "CREMA", "TITANIO",
}


def normalize(value: str) -> str:
    text = str(value or "").upper()
    text = text.replace("MOTOROLA", "MOTO").replace("SAMSUNG ", "")
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    tokens = [token for token in text.split() if token not in IGNORED_WORDS]
    return " ".join(tokens)


def model_key(value: str) -> str:
    tokens = [token for token in normalize(value).split() if token not in {"GB", "MB", "TB"} and not token.isdigit()]
    return " ".join(tokens)


def canonical_name(value: str) -> str:
    name = re.sub(r"\b(?:NIBLE|PISPO|DISPO)\b", "", str(value or ""), flags=re.IGNORECASE)
    name = re.sub(r"\b([A-Z]\d{2})([468])GB\b", r"\1 \2 GB", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+", " ", name).strip(" .•-|")
    upper = name.upper()
    prefixes = (
        (r"^(?:IPHONE|IPAD|MAGIC KEYBOARD)\b", "APPLE "),
        (r"^(?:GALAXY|S2[1-9]\b|A\d{2}\b)", "SAMSUNG "),
        (r"^REDMI\b", "XIAOMI "),
        (r"^MOTO\b", "MOTOROLA "),
        (r"^PIXEL\b", "GOOGLE "),
    )
    for pattern, prefix in prefixes:
        if re.search(pattern, upper) and not upper.startswith(prefix.strip()):
            return prefix + name
    return name


def ocr_rows(pdf_path: Path) -> list[dict]:
    result = subprocess.run(
        ["xcrun", "swift", str(OCR_SCRIPT), str(pdf_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
        timeout=180,
    )
    return json.loads(result.stdout)


def catalog_entries(rows: list[dict]) -> list[dict]:
    entries = []
    pages = sorted({int(row["page"]) for row in rows})
    for page_number in pages:
        page_rows = [row for row in rows if int(row["page"]) == page_number]
        sparse_page = len(page_rows) < 60
        for price_row in page_rows:
            price_match = PRICE_RE.match(price_row["text"].strip())
            if not price_match:
                continue
            price_center = price_row["y"] + price_row["height"] / 2
            if sparse_page:
                candidates = [
                    row for row in page_rows
                    if not PRICE_RE.match(row["text"].strip())
                    and abs((row["y"] + row["height"] / 2) - price_center) <= 0.013
                ]
            else:
                column = min(3, int(price_row["x"] * 4))
                lower, upper = column * 0.25, (column + 1) * 0.25
                candidates = [
                    row for row in page_rows
                    if lower <= row["x"] < upper
                    and not PRICE_RE.match(row["text"].strip())
                    and abs((row["y"] + row["height"] / 2) - price_center) <= 0.012
                ]
            candidates.sort(key=lambda row: (-row["y"], row["x"]))
            name = " ".join(row["text"] for row in candidates)
            name = canonical_name(re.sub(r"^[^A-Za-z0-9]+", "", name).strip())
            price = float(price_match.group(1).replace(",", ""))
            if len(normalize(name)) < 4 or price <= 1:
                continue
            entries.append({"nombre": name, "precio": price})
    return entries


def match_score(catalog_name: str, product_name: str) -> float:
    catalog_normalized = normalize(catalog_name)
    product_normalized = normalize(product_name)
    catalog_model = model_key(catalog_name)
    product_model = model_key(product_name)
    if catalog_model and catalog_model == product_model:
        return 1.0
    if catalog_model and (catalog_model in product_model or product_model in catalog_model):
        return 0.94
    return max(
        SequenceMatcher(None, catalog_normalized, product_normalized).ratio(),
        SequenceMatcher(None, catalog_model, product_model).ratio(),
    )


def extract_visual_inventory(pdf_path: Path, previous_inventory: dict) -> dict:
    entries = catalog_entries(ocr_rows(pdf_path))
    if len(entries) < 80:
        raise ValueError(f"El catalogo visual solo produjo {len(entries)} renglones; no se actualizo.")

    previous_products = previous_inventory.get("productos", [])
    output = []
    matched_entries = set()
    for product in previous_products:
        scored = [(match_score(entry["nombre"], product.get("nombre", "")), index, entry) for index, entry in enumerate(entries)]
        score, index, entry = max(scored, default=(0, -1, None))
        if not entry or score < 0.86:
            continue
        updated = deepcopy(product)
        updated["precio_lista_m"] = entry["precio"]
        updated["cantidad"] = "1+"
        updated["existencia_minima"] = 1
        updated["disponible"] = True
        output.append(updated)
        matched_entries.add(index)

    for index, entry in enumerate(entries):
        if index in matched_entries:
            continue
        stable_name = re.sub(r"[^A-Z0-9]+", " ", entry["nombre"].upper()).strip()
        digest = hashlib.sha1(stable_name.encode("utf-8")).hexdigest()[:12].upper()
        output.append({
            "id": len(output) + 1,
            "categoria_pdf": "CATALOGO SECUNDARIO",
            "clave_categoria": "SEC",
            "codigo": f"SEC-{digest}",
            "nombre": entry["nombre"],
            "cantidad": "1+",
            "existencia_minima": 1,
            "disponible": True,
            "precio_lista_m": entry["precio"],
            "precio_publico": None,
        })

    for index, product in enumerate(output, 1):
        product["id"] = index
    if len(output) < 100:
        raise ValueError(f"Solo se reconstruyeron {len(output)} productos del catalogo visual; no se actualizo.")
    return {
        "fecha_actualizacion": datetime.now().isoformat(timespec="seconds"),
        "source_pdf": pdf_path.name,
        "source_type": "catalogo_visual_secundario",
        "total_productos": len(output),
        "total_disponibles": len(output),
        "total_agotados": 0,
        "total_omitidos_cero": 0,
        "productos": output,
    }
