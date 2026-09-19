#!/usr/bin/env python3
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path


NS = {"ss": "urn:schemas-microsoft-com:office:spreadsheet"}
SS_INDEX = "{urn:schemas-microsoft-com:office:spreadsheet}Index"
REQUIRED_HEADERS = {"NOMBRE CATEGORIA", "CODIGO", "PRODUCTO", "CANTIDAD", "LISTA M", "LISTA G"}


def cell_values(row: ET.Element) -> list[str]:
    values = []
    column = 1
    for cell in row.findall("ss:Cell", NS):
        target = int(cell.attrib.get(SS_INDEX, column))
        while column < target:
            values.append("")
            column += 1
        data = cell.find("ss:Data", NS)
        values.append((data.text or "").strip() if data is not None else "")
        column += 1
    return values


def number(value: str) -> float:
    clean = re.sub(r"[^0-9.-]", "", str(value or ""))
    return float(clean or 0)


def load_rows(path: Path) -> tuple[list[str], list[list[str]]]:
    text = path.read_bytes().decode("utf-8-sig")
    text = re.sub(r'encoding=["\'][^"\']+["\']', 'encoding="UTF-8"', text, count=1)
    root = ET.fromstring(text.encode("utf-8"))
    rows = [cell_values(row) for row in root.findall(".//ss:Worksheet/ss:Table/ss:Row", NS)]
    header_index = next(
        (index for index, row in enumerate(rows) if REQUIRED_HEADERS.issubset(set(row))),
        None,
    )
    if header_index is None:
        raise ValueError("El XLS no contiene las columnas requeridas para Lista M y G.")
    return rows[header_index], rows[header_index + 1:]


def extract_combined_inventory(path: Path) -> tuple[dict, dict]:
    headers, rows = load_rows(path)
    columns = {header: index for index, header in enumerate(headers)}
    products_m = []
    products_g = []
    omitted_zero = 0

    for row in rows:
        def value(header: str) -> str:
            index = columns[header]
            return row[index].strip() if index < len(row) else ""

        code = value("CODIGO")
        name = value("PRODUCTO")
        quantity = int(number(value("CANTIDAD")))
        if not code or not name:
            continue
        if quantity < 1:
            omitted_zero += 1
            continue

        category = value("NOMBRE CATEGORIA") or "OTROS"
        public_price = round(number(value("PRECIO PUBLICO")), 2) if "PRECIO PUBLICO" in columns else None
        common = {
            "categoria_pdf": category,
            "clave_categoria": value("CATEGORIA"),
            "codigo": code,
            "nombre": name,
            "cantidad": str(quantity),
            "existencia_minima": quantity,
            "disponible": True,
            "precio_publico": public_price,
        }
        for target, price_header in ((products_m, "LISTA M"), (products_g, "LISTA G")):
            price = round(number(value(price_header)), 2)
            if price <= 1:
                continue
            product = dict(common)
            product["id"] = len(target) + 1
            product["precio_lista_m"] = price
            target.append(product)

    def inventory(products: list[dict], list_key: str) -> dict:
        return {
            "fecha_actualizacion": datetime.now().isoformat(timespec="seconds"),
            "source_pdf": path.name,
            "source_type": "xls_combinado_m_g",
            "lista": list_key,
            "total_productos": len(products),
            "total_disponibles": len(products),
            "total_agotados": 0,
            "total_omitidos_cero": omitted_zero,
            "productos": products,
        }

    return inventory(products_m, "M"), inventory(products_g, "G")

