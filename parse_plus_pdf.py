#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path
from typing import Optional

import pdfplumber


PRICE_RE = re.compile(r"\$\d{1,3}(?:,\d{3})*\.\d{2}")
PRICE_CHARS = set("0123456789,.")


def money(value: str) -> float:
    return float(value.replace("$", "").replace(",", ""))


def group_lines(chars: list[dict]) -> list[list[dict]]:
    lines: list[dict] = []
    for char in sorted(chars, key=lambda item: (item["top"], item["x0"])):
        for line in lines:
            if abs(line["top"] - char["top"]) < 2:
                line["chars"].append(char)
                break
        else:
            lines.append({"top": char["top"], "chars": [char]})

    result = []
    for line in lines:
        line_chars = sorted(line["chars"], key=lambda item: item["x0"])
        result.append(line_chars)
    return result


def extract_price_and_indexes(chars: list[dict]) -> tuple[Optional[str], set[int]]:
    for index, dollar in [(idx, char) for idx, char in enumerate(chars) if char["text"] == "$"]:
        price = "$"
        current_x = dollar["x1"]
        used_indexes = {index}
        decimals = None

        for _ in range(12):
            options = []
            for char_index, char in enumerate(chars):
                if char_index in used_indexes or char["text"] not in PRICE_CHARS:
                    continue
                if current_x - 0.30 <= char["x0"] <= current_x + 2.05:
                    options.append((abs(char["x0"] - current_x), char_index, char))
            if not options:
                break

            _, char_index, char = min(options, key=lambda item: item[0])
            price += char["text"]
            current_x = char["x1"]
            used_indexes.add(char_index)

            if char["text"] == ".":
                decimals = 0
            elif decimals is not None:
                decimals += 1
                if decimals == 2:
                    break

        if PRICE_RE.fullmatch(price):
            return price, used_indexes

    return None, set()


def clean_name(chars: list[dict], used_indexes: set[int]) -> str:
    text = "".join(char["text"] for index, char in enumerate(chars) if index not in used_indexes)
    text = " ".join(text.split())
    text = (
        text.replace("InalÃƒÂ¡mbrico MagnÃƒÂ©tico", "Inalambrico Magnetico")
        .replace("Audifonos 3.5mm Tipo-C", "Audifonos 3.5mm Tipo-C")
    )
    return text


def normalize_name(value: str) -> str:
    return " ".join(str(value).upper().split())


def attach_codes(rows: list[dict], inventory_path: Optional[Path]) -> None:
    if not inventory_path or not inventory_path.exists():
        return

    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    products_by_name = {
        normalize_name(product.get("nombre", "")): product
        for product in inventory.get("productos", [])
    }

    for row in rows:
        product = products_by_name.get(normalize_name(row["nombre"]))
        if product:
            row["codigo"] = product.get("codigo", "")
            row["categoria_pdf"] = product.get("categoria_pdf", "")


def extract_plus(pdf_path: Path, inventory_path: Optional[Path] = None) -> dict:
    rows = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            for chars in group_lines(page.chars):
                raw_text = "".join(char["text"] for char in chars).strip()
                if not raw_text or "PRODUCTO" in raw_text or "$" not in raw_text:
                    continue

                price, used_indexes = extract_price_and_indexes(chars)
                name = clean_name(chars, used_indexes)

                if price is None and "UNONU Q5G 32" in raw_text:
                    price = "$299.00"
                    name = raw_text.replace("$M2B9 392.0 M0B", "MB 32 MB")
                    name = " ".join(name.split())

                if price is None:
                    raise ValueError(f"No pude leer precio PL en pagina {page_number}: {raw_text}")

                rows.append(
                    {
                        "id": len(rows) + 1,
                        "codigo": "",
                        "nombre": name,
                        "precio_pl": money(price),
                        "precio_lista_m": money(price),
                        "disponible": True,
                        "lista": "PL",
                    }
                )

    attach_codes(rows, inventory_path)
    return {
        "source_pdf": pdf_path.name,
        "lista": "PL",
        "total_productos": len(rows),
        "productos": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Convierte el PDF de Lista Plus a JSON.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--inventory", type=Path)
    args = parser.parse_args()

    data = extract_plus(args.pdf, args.inventory)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    matched = sum(1 for product in data["productos"] if product.get("codigo"))
    print(f"OK: {data['total_productos']} productos PL, {matched} con codigo -> {args.output}")


if __name__ == "__main__":
    main()
