#!/usr/bin/env python3
import argparse
import json
import re
from datetime import datetime
from pathlib import Path

import pdfplumber


ROW_RE = re.compile(
    r"^(?P<cat>\S+)\s+"
    r"(?P<codigo>\S+)\s+"
    r"(?P<producto>.+?)\s+"
    r"(?P<cantidad>\d+\+?)\s+"
    r"\$\s*(?P<lista>[\d,]+\.\d{2})\s+"
    r"\$\s*(?P<publico>[\d,]+\.\d{2})$"
)

FOOTER_RE = re.compile(r"\(\s*(?P<date>\d{2}/\d{2}/\d{4})\s+-\s+(?P<time>\d{2}:\d{2}:\d{2})\s*\)")


def money(value: str) -> float:
    return float(value.replace(",", ""))


def stock_value(raw: str) -> int:
    return int(raw.rstrip("+"))


def extract_inventory(pdf_path: Path) -> dict:
    rows = []
    current_section = None
    report_datetime = None

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
            for raw_line in text.splitlines():
                line = " ".join(raw_line.split())
                if not line or line.startswith("Reporte de") or line.startswith("Cat..."):
                    continue

                footer = FOOTER_RE.search(line)
                if footer and report_datetime is None:
                    date_part = footer.group("date")
                    time_part = footer.group("time")
                    report_datetime = datetime.strptime(
                        f"{date_part} {time_part}", "%d/%m/%Y %H:%M:%S"
                    ).isoformat()
                    continue

                match = ROW_RE.match(line)
                if not match:
                    current_section = line
                    continue

                data = match.groupdict()
                rows.append(
                    {
                        "id": len(rows) + 1,
                        "categoria_pdf": current_section,
                        "clave_categoria": data["cat"],
                        "codigo": data["codigo"],
                        "nombre": data["producto"],
                        "cantidad": data["cantidad"],
                        "existencia_minima": stock_value(data["cantidad"]),
                        "disponible": stock_value(data["cantidad"]) > 0,
                        "precio_lista_m": money(data["lista"]),
                        "precio_publico": money(data["publico"]),
                    }
                )

    return {
        "source_pdf": str(pdf_path),
        "report_datetime": report_datetime,
        "total_productos": len(rows),
        "total_disponibles": sum(1 for row in rows if row["disponible"]),
        "total_agotados": sum(1 for row in rows if not row["disponible"]),
        "productos": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Convierte el PDF diario de inventario a JSON.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()

    inventory = extract_inventory(args.pdf)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"OK: {inventory['total_productos']} productos, "
        f"{inventory['total_disponibles']} disponibles, "
        f"{inventory['total_agotados']} agotados -> {args.output}"
    )


if __name__ == "__main__":
    main()
