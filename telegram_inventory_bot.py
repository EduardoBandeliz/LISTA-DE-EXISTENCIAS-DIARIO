#!/usr/bin/env python3
import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional, Union
from urllib.parse import quote

from telegram import Bot, Update
from telegram.error import Conflict, NetworkError, RetryAfter, TelegramError, TimedOut
from telegram.request import HTTPXRequest
from PIL import Image, ImageOps

from parse_inventory_pdf import extract_inventory
from parse_plus_pdf import extract_plus
from parse_payjoy_excel import extract_payjoy_excel


ROOT = Path(__file__).resolve().parent
INVENTORY_JSON = ROOT / "inventario.json"
PLUS_JSON = ROOT / "plus.json"
LISTA_G_JSON = ROOT / "listag.json"
PAYJOY_JSON = ROOT / "payjoy-payphone.json"
PRODUCT_IMAGES_JSON = ROOT / "product-images.json"
PRODUCT_IMAGE_DIR = ROOT / "img" / "celulares"
PENDING_IMAGE_STATE_JSON = ROOT / ".telegram-image-state.json"
REPORT_STATE_JSON = ROOT / ".telegram-report-state.json"
AUTO_PUBLISH_STATE_JSON = ROOT / ".telegram-auto-publish-state.json"
NETLIFY_MONITOR_STATE_JSON = ROOT / ".netlify-monitor-state.json"
OPERATIONS_STATE_JSON = ROOT / ".operations-state.json"
BACKUP_DIR = ROOT / "backups"
PDF_NAME_CONTAINS = os.getenv("PDF_NAME_CONTAINS", "").lower().strip()
ALLOWED_CHAT_ID = os.getenv("ALLOWED_CHAT_ID", "").strip()
BROADCAST_CHAT_IDS = [
    chat_id.strip()
    for chat_id in os.getenv("BROADCAST_CHAT_IDS", "").replace(";", ",").split(",")
    if chat_id.strip()
]
NETLIFY_SITE_URL = os.getenv("NETLIFY_SITE_URL", "https://listadeexistenciasdiario.netlify.app/").strip()
GOOGLE_SHEETS_SPREADSHEET_ID = os.getenv(
    "GOOGLE_SHEETS_SPREADSHEET_ID",
    "1eSgzIz6sKCkOZ8JttDH_Vks5pZM92moHfDs3ToEoGfE",
).strip()
GOOGLE_SHEETS_CREDENTIALS_FILE = os.getenv("GOOGLE_SHEETS_CREDENTIALS_FILE", "").strip()
GOOGLE_SHEETS_ENABLED = os.getenv("GOOGLE_SHEETS_ENABLED", "0").strip().lower() in {"1", "true", "yes"}
TELEGRAM_TIMEOUT_SECONDS = 120
TELEGRAM_MEDIA_TIMEOUT_SECONDS = 300
AUTO_PUBLISH_IMAGES_AT = os.getenv("AUTO_PUBLISH_IMAGES_AT", "21:00").strip()
AUTO_PUBLISH_ENABLED = os.getenv("AUTO_PUBLISH_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
NETLIFY_MONITOR_ENABLED = os.getenv("NETLIFY_MONITOR_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
NETLIFY_MONITOR_INTERVAL_SECONDS = max(60, int(os.getenv("NETLIFY_MONITOR_INTERVAL_SECONDS", "300") or 300))
INVENTORY_MIN_PRODUCTS = max(1, int(os.getenv("INVENTORY_MIN_PRODUCTS", "50") or 50))
INVENTORY_MAX_DROP_PERCENT = min(95, max(10, int(os.getenv("INVENTORY_MAX_DROP_PERCENT", "50") or 50)))
BACKUP_RETENTION = max(5, int(os.getenv("BACKUP_RETENTION", "15") or 15))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
PENDING_ADVERTISEMENT_CHATS: set[str] = set()
IMPORTANT_PRICE_CHANGE_AMOUNT = float(os.getenv("IMPORTANT_PRICE_CHANGE_AMOUNT", "100") or 100)
IMPORTANT_PRICE_CHANGE_PERCENT = float(os.getenv("IMPORTANT_PRICE_CHANGE_PERCENT", "5") or 5)
OPPORTUNITY_PRICE_DROP_AMOUNT = float(
    os.getenv("OPPORTUNITY_PRICE_DROP_AMOUNT", str(IMPORTANT_PRICE_CHANGE_AMOUNT))
    or IMPORTANT_PRICE_CHANGE_AMOUNT
)
OPPORTUNITY_PRICE_DROP_PERCENT = float(
    os.getenv("OPPORTUNITY_PRICE_DROP_PERCENT", str(IMPORTANT_PRICE_CHANGE_PERCENT))
    or IMPORTANT_PRICE_CHANGE_PERCENT
)
CELLPHONE_BRANDS = {
    "Apple",
    "Samsung",
    "Motorola",
    "Xiaomi",
    "BLU",
    "Infinix",
    "Tecno",
    "Realme",
    "Honor",
    "Itel",
    "Cubot",
    "Google",
    "Naomi",
    "Qtouch",
    "Blackview",
    "Techview",
    "Logic",
}
ACCESSORY_GROUPS = {
    "ACCESORIOS BODEGA",
    "ACCESORIOS",
    "ADAPTADORES",
    "BUYTITI",
    "CABLES",
    "DEMOS",
    "MISCELANEOS",
    "MOREKA",
    "PROMOCIONALES",
    "SENWA",
    "STEREN",
    "UNONU",
    "ONONU",
    "ZTE",
}
KNOWN_BRANDS = [
    "Apple",
    "Samsung",
    "Motorola",
    "Xiaomi",
    "BLU",
    "Infinix",
    "Tecno",
    "Realme",
    "Honor",
    "Itel",
    "Cubot",
    "Google",
    "Naomi",
    "Qtouch",
    "Blackview",
]
GOOGLE_SHEET_TABS = {
    "M": os.getenv("GOOGLE_SHEETS_TAB_M", "Existencias").strip() or "Existencias",
    "G": os.getenv("GOOGLE_SHEETS_TAB_G", "LISTA G").strip() or "LISTA G",
    "PL": os.getenv("GOOGLE_SHEETS_TAB_PL", "LISTA PL").strip() or "LISTA PL",
}


def run(command: list[str]) -> str:
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def load_inventory() -> dict:
    if not INVENTORY_JSON.exists():
        return {"productos": []}
    return json.loads(INVENTORY_JSON.read_text(encoding="utf-8"))


def load_product_images() -> dict:
    if not PRODUCT_IMAGES_JSON.exists():
        return {}
    return json.loads(PRODUCT_IMAGES_JSON.read_text(encoding="utf-8"))


def load_plus_inventory() -> dict:
    if not PLUS_JSON.exists():
        return {"productos": []}
    return json.loads(PLUS_JSON.read_text(encoding="utf-8"))


def load_lista_g_inventory() -> dict:
    if not LISTA_G_JSON.exists():
        return {"productos": []}
    return json.loads(LISTA_G_JSON.read_text(encoding="utf-8"))


LIST_FILES = {
    "M": INVENTORY_JSON,
    "G": LISTA_G_JSON,
    "PL": PLUS_JSON,
    "PAYJOY": PAYJOY_JSON,
}


def read_operations_state() -> dict:
    if not OPERATIONS_STATE_JSON.exists():
        return {}
    try:
        return json.loads(OPERATIONS_STATE_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_operations_state(state: dict) -> None:
    OPERATIONS_STATE_JSON.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_inventory_update(new_inventory: dict, current_path: Path, list_name: str) -> None:
    products = new_inventory.get("productos")
    if not isinstance(products, list) or len(products) < INVENTORY_MIN_PRODUCTS:
        raise ValueError(
            f"Proteccion activa: {list_name} contiene solo {len(products) if isinstance(products, list) else 0} productos; no se publico."
        )
    missing_names = [p for p in products if not str(p.get("nombre", "")).strip()]
    invalid_prices = [p for p in products if float(p.get("precio_lista_m", p.get("precio", 0)) or 0) <= 0]
    if missing_names:
        raise ValueError(f"Proteccion activa: {len(missing_names)} productos de {list_name} no tienen nombre.")
    if len(invalid_prices) > max(5, int(len(products) * 0.05)):
        raise ValueError(f"Proteccion activa: {len(invalid_prices)} productos de {list_name} no tienen precio valido.")
    if current_path.exists():
        try:
            current_count = len(json.loads(current_path.read_text(encoding="utf-8")).get("productos", []))
        except (json.JSONDecodeError, OSError):
            current_count = 0
        if current_count >= INVENTORY_MIN_PRODUCTS:
            drop_percent = (current_count - len(products)) * 100 / current_count
            if drop_percent > INVENTORY_MAX_DROP_PERCENT:
                raise ValueError(
                    f"Proteccion activa: {list_name} bajaria de {current_count} a {len(products)} productos ({drop_percent:.0f}% menos); no se publico."
                )


def backup_file(source: Path, list_key: str) -> Optional[Path]:
    if not source.exists() or source.stat().st_size <= 2:
        return None
    target_dir = BACKUP_DIR / list_key.upper()
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target = target_dir / f"{stamp}-{source.name}"
    shutil.copy2(source, target)
    backups = sorted(target_dir.glob(f"*-{source.name}"), reverse=True)
    for old in backups[BACKUP_RETENTION:]:
        old.unlink(missing_ok=True)
    return target


def record_successful_update(list_key: str, count: int, source_name: str) -> None:
    state = read_operations_state()
    updates = state.setdefault("last_updates", {})
    updates[list_key.upper()] = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "count": count,
        "source": source_name,
    }
    write_operations_state(state)


def daily_backup_if_needed() -> bool:
    state = read_operations_state()
    today = datetime.now().date().isoformat()
    if state.get("last_daily_backup") == today:
        return False
    for key, path in LIST_FILES.items():
        backup_file(path, key)
    backup_file(PRODUCT_IMAGES_JSON, "IMAGES")
    state["last_daily_backup"] = today
    state["last_daily_backup_at"] = datetime.now().isoformat(timespec="seconds")
    write_operations_state(state)
    return True


def restore_latest_backup(list_key: str) -> str:
    key = list_key.upper().strip()
    aliases = {"LISTAM": "M", "LISTAG": "G", "PLUS": "PL", "PAYPHONE": "PAYJOY"}
    key = aliases.get(key, key)
    if key not in LIST_FILES:
        raise ValueError("Indica una lista: /restaurar M, /restaurar G, /restaurar PL o /restaurar PAYJOY")
    destination = LIST_FILES[key]
    backups = sorted((BACKUP_DIR / key).glob(f"*-{destination.name}"), reverse=True)
    if not backups:
        raise ValueError(f"No hay respaldos disponibles para {key}.")
    backup_file(destination, f"{key}-ANTES-RESTAURAR")
    shutil.copy2(backups[0], destination)
    restored = json.loads(destination.read_text(encoding="utf-8"))
    validate_inventory_update(restored, Path("/archivo-que-no-existe"), key)
    run(["git", "add", destination.name])
    run(["git", "commit", "-m", f"Restaurar lista {key} desde respaldo"])
    run(["git", "push", "origin", "main"])
    count = len(restored.get("productos", []))
    record_successful_update(key, count, f"respaldo {backups[0].name}")
    return f"Lista {key} restaurada con {count} productos. Netlify publicara el cambio automaticamente."


def write_payjoy_data(inventory: dict) -> str:
    validate_inventory_update(inventory, PAYJOY_JSON, "Payjoy - Payphone")
    backup_file(PAYJOY_JSON, "PAYJOY")
    PAYJOY_JSON.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    record_successful_update("PAYJOY", inventory["total_productos"], inventory.get("source_excel", "Excel"))
    return f"{inventory['total_productos']} equipos Payjoy - Payphone"


def publish_payjoy_to_github(summary: str) -> str:
    status = run(["git", "status", "--short", PAYJOY_JSON.name])
    if not status:
        return "La lista Equipos Payjoy - Payphone no tuvo cambios."
    run(["git", "add", PAYJOY_JSON.name])
    run(["git", "commit", "-m", f"Actualizar Equipos Payjoy - Payphone ({summary})"])
    run(["git", "push", "origin", "main"])
    return "Lista Equipos Payjoy - Payphone actualizada en GitHub. Netlify publicara el cambio automaticamente."


def inventory_summary(inventory: dict) -> str:
    omitted = int(inventory.get("total_omitidos_cero", 0) or 0)
    omitted_text = f", {omitted} omitidos con existencia 0" if omitted else ""
    return (
        f"{inventory['total_productos']} productos, "
        f"{inventory['total_disponibles']} disponibles"
        f"{omitted_text}"
    )


def write_inventory_data(inventory: dict) -> str:
    validate_inventory_update(inventory, INVENTORY_JSON, "Lista M")
    backup_file(INVENTORY_JSON, "M")
    INVENTORY_JSON.write_text(
        __import__("json").dumps(inventory, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    record_successful_update("M", inventory["total_productos"], inventory.get("source_pdf", "PDF"))
    return inventory_summary(inventory)


def sync_from_github() -> None:
    stashed_images = False
    if pending_image_status():
        run(["git", "stash", "push", "-u", "-m", "imagenes pendientes del bot", "--", "product-images.json", "img/celulares"])
        stashed_images = True

    try:
        # If a previous run failed after writing inventario.json, discard that
        # generated file before pulling. The current PDF will regenerate it.
        run(["git", "restore", "--", "inventario.json"])
        if PAYJOY_JSON.exists():
            run(["git", "restore", "--", PAYJOY_JSON.name])
        run(["git", "pull", "--rebase", "origin", "main"])
    finally:
        if stashed_images:
            run(["git", "stash", "pop"])


def publish_to_github(summary: str) -> str:
    status = run(["git", "status", "--short", "inventario.json"])
    if not status:
        return "El inventario no tuvo cambios."

    run(["git", "add", "inventario.json"])
    run(["git", "commit", "-m", f"Actualizar inventario diario ({summary})"])
    run(["git", "push", "origin", "main"])
    return "Inventario actualizado en GitHub. Netlify publicara el cambio automaticamente."


def write_plus_data(plus_inventory: dict) -> str:
    validate_inventory_update(plus_inventory, PLUS_JSON, "Lista PL")
    backup_file(PLUS_JSON, "PL")
    PLUS_JSON.write_text(
        json.dumps(plus_inventory, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    record_successful_update("PL", plus_inventory["total_productos"], plus_inventory.get("source_pdf", "PDF"))
    return f"{plus_inventory['total_productos']} productos PL"


def publish_plus_to_github(summary: str) -> str:
    status = run(["git", "status", "--short", "plus.json"])
    if not status:
        return "La lista PL no tuvo cambios."

    run(["git", "add", "plus.json"])
    run(["git", "commit", "-m", f"Actualizar lista PL ({summary})"])
    run(["git", "push", "origin", "main"])
    return "Lista PL actualizada en GitHub. Netlify publicara el cambio automaticamente."


def write_lista_g_data(inventory: dict) -> str:
    validate_inventory_update(inventory, LISTA_G_JSON, "Lista G")
    backup_file(LISTA_G_JSON, "G")
    LISTA_G_JSON.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    record_successful_update("G", inventory["total_productos"], inventory.get("source_pdf", "PDF"))
    return f"{inventory['total_productos']} productos Lista G"


def publish_lista_g_to_github(summary: str) -> str:
    status = run(["git", "status", "--short", "listag.json"])
    if not status:
        return "La Lista G no tuvo cambios."

    run(["git", "add", "listag.json"])
    run(["git", "commit", "-m", f"Actualizar Lista G ({summary})"])
    run(["git", "push", "origin", "main"])
    return "Lista G actualizada en GitHub. Netlify publicara el cambio automaticamente."


def google_sheets_configured() -> bool:
    return (
        GOOGLE_SHEETS_ENABLED
        and bool(GOOGLE_SHEETS_SPREADSHEET_ID)
        and bool(GOOGLE_SHEETS_CREDENTIALS_FILE)
        and Path(GOOGLE_SHEETS_CREDENTIALS_FILE).exists()
    )


def google_sheets_service():
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    credentials = Credentials.from_service_account_file(
        GOOGLE_SHEETS_CREDENTIALS_FILE,
        scopes=scopes,
    )
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def ensure_google_sheet_tab(service, tab_name: str) -> None:
    metadata = service.spreadsheets().get(
        spreadsheetId=GOOGLE_SHEETS_SPREADSHEET_ID,
        fields="sheets(properties(title))",
    ).execute()
    titles = {
        sheet.get("properties", {}).get("title")
        for sheet in metadata.get("sheets", [])
    }
    if tab_name in titles:
        return
    service.spreadsheets().batchUpdate(
        spreadsheetId=GOOGLE_SHEETS_SPREADSHEET_ID,
        body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
    ).execute()


def sheet_safe_value(value) -> str:
    if value is None:
        return ""
    return str(value)


def google_sheet_rows(inventory: dict, list_key: str) -> list[list]:
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    headers = [
        "Codigo",
        "Producto",
        "Cantidad",
        "Precio lista",
        "Precio publico",
        "Fecha actualizacion",
    ]
    rows = [headers]
    for product in inventory.get("productos", []):
        price = product.get("precio_pl", product.get("precio_lista_m", product.get("precio", "")))
        if float(price or 0) <= 1:
            continue
        rows.append(
            [
                sheet_safe_value(product.get("codigo", "")),
                sheet_safe_value(product.get("nombre", "")),
                sheet_safe_value(product.get("cantidad", "")),
                price,
                product.get("precio_publico", ""),
                updated_at,
            ]
        )
    return rows


def sync_inventory_to_google_sheets(inventory: dict, list_key: str) -> str:
    if not google_sheets_configured():
        return "Google Sheets no configurado."

    tab_name = GOOGLE_SHEET_TABS.get(list_key, list_key)
    service = google_sheets_service()
    ensure_google_sheet_tab(service, tab_name)
    rows = google_sheet_rows(inventory, list_key)

    service.spreadsheets().values().clear(
        spreadsheetId=GOOGLE_SHEETS_SPREADSHEET_ID,
        range=f"'{tab_name}'!A:Z",
        body={},
    ).execute()
    service.spreadsheets().values().update(
        spreadsheetId=GOOGLE_SHEETS_SPREADSHEET_ID,
        range=f"'{tab_name}'!A1",
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()
    return f"Google Sheets actualizado: {tab_name} ({len(rows) - 1} productos)."


def product_codes() -> set[str]:
    data = load_inventory()
    return {str(item.get("codigo", "")).strip() for item in data.get("productos", []) if item.get("codigo")}


def infer_brand(name: str, fallback: str) -> str:
    fallback_upper = str(fallback or "").upper()
    name_upper = str(name or "").upper()
    if any(group in fallback_upper or group in name_upper for group in ACCESSORY_GROUPS):
        return "Accesorios"
    for brand in KNOWN_BRANDS:
        if brand.upper() in name_upper:
            return brand
    return fallback or "Otros"


def normalized_brand(product: dict) -> str:
    return str(product.get("marca") or infer_brand(product.get("nombre", ""), product.get("categoria_pdf", ""))).strip()


def is_cellphone(product: dict) -> bool:
    brand = normalized_brand(product)
    name_upper = str(product.get("nombre", "")).upper()
    if brand == "Apple":
        return "IPHONE" in name_upper
    return brand in CELLPHONE_BRANDS


def product_image(product: dict, images: dict) -> str:
    return images.get(str(product.get("codigo", "")).strip()) or images.get(product.get("nombre", "")) or ""


def product_label(product: dict) -> str:
    code = str(product.get("codigo", "")).strip()
    name = str(product.get("nombre", "")).strip()
    price = product.get("precio_lista_m", product.get("precio", 0))
    return f"{code} - {name} - ${float(price or 0):,.2f}"


def normalize_text(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper()).strip()


def compact_model_name(name: str) -> str:
    text = normalize_text(name)
    color_words = {
        "AZUL", "NEGRO", "BLANCO", "GRIS", "VERDE", "VIOLETA", "MORADO", "ROSA",
        "DORADO", "PLATA", "NARANJA", "ROJO", "AMARILLO", "CREMA", "LATINO",
        "NACIONAL", "PJ", "ESIM", "CLARO", "CIELO", "TITANEO",
    }
    tokens = [token for token in text.split() if token not in color_words]
    return " ".join(tokens)


def find_product(query: str, inventory: Optional[dict] = None) -> Optional[dict]:
    inventory = inventory or load_inventory()
    clean_query = normalize_text(query)
    if not clean_query:
        return None
    for product in inventory.get("productos", []):
        if normalize_text(str(product.get("codigo", ""))) == clean_query:
            return product
    matches = [
        product
        for product in inventory.get("productos", [])
        if clean_query in normalize_text(product.get("nombre", ""))
    ]
    return matches[0] if matches else None


def plus_price_for_product(product: dict) -> Optional[float]:
    plus_inventory = load_plus_inventory()
    code = str(product.get("codigo", "")).strip()
    product_name = normalize_text(product.get("nombre", ""))
    for plus_product in plus_inventory.get("productos", []):
        if code and str(plus_product.get("codigo", "")).strip() == code:
            return float(plus_product.get("precio_pl", plus_product.get("precio_lista_m", 0)) or 0)
    for plus_product in plus_inventory.get("productos", []):
        if normalize_text(plus_product.get("nombre", "")) == product_name:
            return float(plus_product.get("precio_pl", plus_product.get("precio_lista_m", 0)) or 0)
    return None


def lista_g_price_for_product(product: dict) -> Optional[float]:
    lista_g_inventory = load_lista_g_inventory()
    code = str(product.get("codigo", "")).strip()
    product_name = normalize_text(product.get("nombre", ""))
    for lista_g_product in lista_g_inventory.get("productos", []):
        if code and str(lista_g_product.get("codigo", "")).strip() == code:
            return float(lista_g_product.get("precio_lista_m", lista_g_product.get("precio", 0)) or 0)
    for lista_g_product in lista_g_inventory.get("productos", []):
        if normalize_text(lista_g_product.get("nombre", "")) == product_name:
            return float(lista_g_product.get("precio_lista_m", lista_g_product.get("precio", 0)) or 0)
    return None


def product_message(query: str) -> str:
    product = find_product(query)
    if not product:
        return f"No encontre producto para: {query}"

    images = load_product_images()
    price = price_value(product)
    dse_price = max(0, price - 30)
    pl_price = plus_price_for_product(product)
    lista_g_price = lista_g_price_for_product(product)
    image_status = "Si" if product_image(product, images) else "No"
    availability = "Disponible" if product.get("disponible") else "Agotado"
    search_term = quote(str(product.get("nombre", "")))
    lines = [
        f"Producto: {product.get('nombre')}",
        f"Codigo: {product.get('codigo')}",
        f"Marca/grupo: {normalized_brand(product)}",
        f"Estatus: {availability}",
        f"Precio normal: ${price:,.2f}",
        f"Precio DSE: ${dse_price:,.2f}",
        f"Precio PL: ${pl_price:,.2f}" if pl_price is not None else "Precio PL: No disponible",
        f"Precio Lista G: ${lista_g_price:,.2f}" if lista_g_price is not None else "Precio Lista G: No disponible",
        f"Tiene imagen: {image_status}",
        "",
        f"Liga celulares:\n{NETLIFY_SITE_URL.rstrip('/')}?celulares=1",
        f"Busqueda sugerida:\n{NETLIFY_SITE_URL.rstrip('/')}?celulares=1&q={search_term}",
    ]
    return "\n".join(lines)


def missing_cellphone_images() -> list[dict]:
    inventory = load_inventory()
    images = load_product_images()
    missing = [
        product
        for product in inventory.get("productos", [])
        if is_cellphone(product) and not product_image(product, images)
    ]
    missing.sort(key=lambda product: (normalized_brand(product), str(product.get("nombre", ""))))
    return missing


def missing_images_message(limit: int = 35) -> str:
    missing = missing_cellphone_images()
    if not missing:
        return "Todos los celulares del inventario actual ya tienen imagen."
    visible_missing = missing[:limit]
    lines = [
        f"Celulares sin imagen: {len(missing)}",
        f"Mostrando primeros {len(visible_missing)}:",
        "",
        *[product_label(product) for product in visible_missing],
    ]
    if len(missing) > limit:
        lines.append(f"...y {len(missing) - limit} más.")
    return "\n".join(lines)


def whatsapp_message() -> str:
    return (
        "Lista de celulares actualizada:\n"
        f"{NETLIFY_SITE_URL.rstrip('/')}?celulares=1"
    )


def payjoy_whatsapp_messages(max_length: int = 3600) -> list[str]:
    inventory = json.loads(PAYJOY_JSON.read_text(encoding="utf-8")) if PAYJOY_JSON.exists() else {"productos": []}
    products = inventory.get("productos", [])
    if not products:
        return ["Todavia no hay equipos en la lista Payjoy - Payphone."]

    header = (
        "📱✨ *EQUIPOS PAYJOY - PAYPHONE* ✨📱\n"
        "💳 Opciones para estrenar equipo\n"
        "💰 Precios actualizados:\n\n"
    )
    footer = (
        "\n🔎 *CATÁLOGOS:*\n"
        "📦 Equipos con existencia:\n"
        f"{NETLIFY_SITE_URL.rstrip('/')}?celulares=1\n\n"
        "💳 Equipos Payjoy - Payphone:\n"
        f"{NETLIFY_SITE_URL.rstrip('/')}?payjoy=1\n\n"
        "📲 Pregunta por disponibilidad y condiciones."
    )
    messages: list[str] = []
    current = header
    current_brand = ""
    for product in products:
        name = str(product.get("nombre", "")).strip()
        price = float(product.get("precio_payjoy", product.get("precio_lista_m", 0)) or 0)
        brand = name.split(maxsplit=1)[0].upper() if name else "OTROS"
        brand_line = f"\n🔹 *{brand}*\n" if brand != current_brand else ""
        product_line = f"📱 {name} — *${price:,.2f}*\n"
        addition = brand_line + product_line
        if len(current) + len(addition) + len(footer) > max_length and current != header:
            messages.append(current.rstrip())
            current = brand_line.lstrip("\n") + product_line
        else:
            current += addition
        current_brand = brand
    current += footer
    messages.append(current.rstrip())
    return messages


def google_sheets_status_message() -> str:
    credentials_path = Path(GOOGLE_SHEETS_CREDENTIALS_FILE) if GOOGLE_SHEETS_CREDENTIALS_FILE else None
    lines = [
        "Estado Google Sheets:",
        f"Activo: {'Si' if GOOGLE_SHEETS_ENABLED else 'No'}",
        f"Spreadsheet ID: {GOOGLE_SHEETS_SPREADSHEET_ID or 'No configurado'}",
        f"Credencial: {GOOGLE_SHEETS_CREDENTIALS_FILE or 'No configurada'}",
        f"Archivo existe: {'Si' if credentials_path and credentials_path.exists() else 'No'}",
        f"Pestañas: M={GOOGLE_SHEET_TABS['M']}, G={GOOGLE_SHEET_TABS['G']}, PL={GOOGLE_SHEET_TABS['PL']}",
    ]
    return "\n".join(lines)


def inventory_by_code(inventory: dict) -> dict[str, dict]:
    return {
        str(product.get("codigo", "")).strip(): product
        for product in inventory.get("productos", [])
        if product.get("codigo")
    }


def price_value(product: dict) -> float:
    return float(product.get("precio_lista_m", product.get("precio", 0)) or 0)


def analyze_inventory_update(previous_inventory: dict, new_inventory: dict) -> dict:
    previous_by_code = inventory_by_code(previous_inventory)
    new_by_code = inventory_by_code(new_inventory)

    new_products = [
        product
        for code, product in new_by_code.items()
        if code not in previous_by_code
    ]
    price_changes = []
    for code, product in new_by_code.items():
        previous = previous_by_code.get(code)
        if not previous:
            continue
        old_price = price_value(previous)
        new_price = price_value(product)
        if old_price != new_price:
            price_changes.append((product, old_price, new_price))

    restocked = [
        product
        for code, product in new_by_code.items()
        if previous_by_code.get(code)
        and not previous_by_code[code].get("disponible")
        and product.get("disponible")
    ]
    depleted = [
        product
        for code, product in new_by_code.items()
        if previous_by_code.get(code)
        and previous_by_code[code].get("disponible")
        and not product.get("disponible")
    ]

    images = load_product_images()
    new_without_image = [
        product for product in new_products if is_cellphone(product) and not product_image(product, images)
    ]
    important_price_changes = []
    opportunities = []
    for product, old_price, new_price in price_changes:
        amount = abs(new_price - old_price)
        percent = (amount / old_price * 100) if old_price else 100
        if amount >= IMPORTANT_PRICE_CHANGE_AMOUNT or percent >= IMPORTANT_PRICE_CHANGE_PERCENT:
            important_price_changes.append((product, old_price, new_price, amount, percent))
        if new_price < old_price:
            drop_amount = old_price - new_price
            drop_percent = (drop_amount / old_price * 100) if old_price else 100
            if drop_amount >= OPPORTUNITY_PRICE_DROP_AMOUNT or drop_percent >= OPPORTUNITY_PRICE_DROP_PERCENT:
                opportunities.append((product, old_price, new_price, drop_amount, drop_percent))
    opportunities.sort(key=lambda item: (item[3], item[4]), reverse=True)

    return {
        "new_products": new_products,
        "price_changes": price_changes,
        "important_price_changes": important_price_changes,
        "opportunities": opportunities,
        "new_without_image": new_without_image,
        "restocked": restocked,
        "depleted": depleted,
    }


def serialize_product(product: dict) -> dict:
    return {
        "codigo": str(product.get("codigo", "")),
        "nombre": str(product.get("nombre", "")),
        "precio_lista_m": price_value(product),
        "disponible": bool(product.get("disponible", True)),
        "marca": normalized_brand(product),
    }


def save_update_report_state(analysis: dict, summary: str) -> None:
    state = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "new_products": [serialize_product(product) for product in analysis["new_products"]],
        "new_without_image": [serialize_product(product) for product in analysis["new_without_image"]],
        "important_price_changes": [
            {
                "producto": serialize_product(product),
                "precio_anterior": old_price,
                "precio_nuevo": new_price,
                "cambio": amount,
                "porcentaje": percent,
            }
            for product, old_price, new_price, amount, percent in analysis["important_price_changes"]
        ],
        "opportunities": [
            {
                "producto": serialize_product(product),
                "precio_anterior": old_price,
                "precio_nuevo": new_price,
                "ahorro": amount,
                "porcentaje": percent,
            }
            for product, old_price, new_price, amount, percent in analysis["opportunities"]
        ],
        "restocked": [serialize_product(product) for product in analysis["restocked"]],
        "depleted": [serialize_product(product) for product in analysis["depleted"]],
    }
    REPORT_STATE_JSON.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_update_report_state() -> dict:
    if not REPORT_STATE_JSON.exists():
        return {}
    try:
        return json.loads(REPORT_STATE_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def build_update_report(previous_inventory: dict, new_inventory: dict, limit: int = 12) -> str:
    analysis = analyze_inventory_update(previous_inventory, new_inventory)
    new_products = analysis["new_products"]
    price_changes = analysis["price_changes"]
    important_price_changes = analysis["important_price_changes"]
    opportunities = analysis["opportunities"]
    new_without_image = analysis["new_without_image"]

    lines = [
        "Reporte de actualización:",
        f"Productos nuevos: {len(new_products)}",
        f"Cambios de precio: {len(price_changes)}",
        f"Alertas importantes: {len(important_price_changes)}",
        f"Oportunidades: {len(opportunities)}",
        f"Nuevos celulares sin imagen: {len(new_without_image)}",
    ]

    if new_products:
        lines.extend(["", "Nuevos:"])
        lines.extend(product_label(product) for product in new_products[:limit])
        if len(new_products) > limit:
            lines.append(f"...y {len(new_products) - limit} más.")

    if price_changes:
        lines.extend(["", "Cambios de precio:"])
        for product, old_price, new_price in price_changes[:limit]:
            direction = "subio" if new_price > old_price else "bajo"
            lines.append(
                f"{product.get('codigo')} - {product.get('nombre')} - "
                f"${old_price:,.2f} -> ${new_price:,.2f} ({direction})"
            )
        if len(price_changes) > limit:
            lines.append(f"...y {len(price_changes) - limit} más.")

    if important_price_changes:
        lines.extend(["", "Alertas importantes:"])
        for product, old_price, new_price, amount, percent in important_price_changes[:limit]:
            direction = "subio" if new_price > old_price else "bajo"
            lines.append(
                f"{product.get('codigo')} - {product.get('nombre')} - "
                f"${old_price:,.2f} -> ${new_price:,.2f} ({direction} ${amount:,.2f}, {percent:.1f}%)"
            )

    if opportunities:
        lines.extend(["", "Lista de oportunidad:"])
        for product, old_price, new_price, amount, percent in opportunities[:limit]:
            lines.append(
                f"{product.get('codigo')} - {product.get('nombre')} - "
                f"${old_price:,.2f} -> ${new_price:,.2f} (ahorro ${amount:,.2f}, {percent:.1f}%)"
            )
        if len(opportunities) > limit:
            lines.append(f"...y {len(opportunities) - limit} más.")

    if new_without_image:
        lines.extend(["", "Nuevos celulares sin imagen:"])
        lines.extend(product_label(product) for product in new_without_image[:limit])

    return "\n".join(lines)


def latest_new_products_message(limit: int = 25) -> str:
    state = load_update_report_state()
    if not state:
        return "Todavia no tengo reporte de productos nuevos guardado."
    new_products = state.get("new_products", [])
    new_without_image = state.get("new_without_image", [])
    alerts = state.get("important_price_changes", [])
    opportunities = state.get("opportunities", [])
    lines = [
        "Ultimo reporte de productos nuevos:",
        state.get("summary", ""),
        f"Productos nuevos: {len(new_products)}",
        f"Nuevos celulares sin imagen: {len(new_without_image)}",
        f"Alertas importantes: {len(alerts)}",
        f"Oportunidades: {len(opportunities)}",
    ]
    if new_products:
        lines.extend(["", "Nuevos:"])
        for product in new_products[:limit]:
            lines.append(f"{product.get('codigo')} - {product.get('nombre')} - ${product.get('precio_lista_m', 0):,.2f}")
        if len(new_products) > limit:
            lines.append(f"...y {len(new_products) - limit} más.")
    if new_without_image:
        lines.extend(["", "Nuevos sin imagen:"])
        for product in new_without_image[:limit]:
            lines.append(f"{product.get('codigo')} - {product.get('nombre')}")
    return "\n".join(line for line in lines if line is not None)


def opportunities_message(limit: int = 25) -> str:
    state = load_update_report_state()
    if not state:
        return "Todavia no tengo reporte de oportunidades guardado. Se generara con el siguiente PDF de inventario."
    opportunities = state.get("opportunities", [])
    if not opportunities:
        return "No detecte oportunidades en el ultimo inventario. Una oportunidad es una baja fuerte de precio."

    lines = [
        "Lista de oportunidad del ultimo inventario:",
        state.get("summary", ""),
        "",
    ]
    for item in opportunities[:limit]:
        product = item.get("producto", {})
        lines.append(
            f"{product.get('codigo')} - {product.get('nombre')} - "
            f"${item.get('precio_anterior', 0):,.2f} -> ${item.get('precio_nuevo', 0):,.2f} "
            f"(ahorro ${item.get('ahorro', 0):,.2f}, {item.get('porcentaje', 0):.1f}%)"
        )
    if len(opportunities) > limit:
        lines.append(f"...y {len(opportunities) - limit} más.")
    return "\n".join(line for line in lines if line is not None)


def extract_product_code(text: str) -> Optional[str]:
    clean = (text or "").strip()
    if not clean:
        return None

    patterns = [
        r"(?:codigo|código)[:\s#-]+([0-9]{6,20})",
        r"(?:codigo|código|cod|sku)[:\s#-]+([A-Za-z0-9._-]{3,40})",
        r"^/imagen\s+([A-Za-z0-9._-]{3,40})",
        r"^([0-9]{6,20})$",
    ]
    for pattern in patterns:
        match = re.search(pattern, clean, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def command_key(text: str) -> str:
    clean = (text or "").strip().lower()
    if not clean.startswith("/"):
        return clean
    parts = clean.split(maxsplit=1)
    command = parts[0].split("@", 1)[0]
    if len(parts) == 1:
        return command
    return f"{command} {parts[1]}"


def read_pending_image_codes() -> dict[str, str]:
    if not PENDING_IMAGE_STATE_JSON.exists():
        return {}
    try:
        data = json.loads(PENDING_IMAGE_STATE_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {str(chat_id): str(code) for chat_id, code in data.items() if code}


def set_pending_image_code(chat_id: str, product_code: str) -> None:
    pending_codes = read_pending_image_codes()
    pending_codes[chat_id] = product_code
    PENDING_IMAGE_STATE_JSON.write_text(
        json.dumps(pending_codes, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def get_pending_image_code(chat_id: str) -> Optional[str]:
    return read_pending_image_codes().get(chat_id)


def clear_pending_image_code(chat_id: str) -> None:
    pending_codes = read_pending_image_codes()
    pending_codes.pop(chat_id, None)
    PENDING_IMAGE_STATE_JSON.write_text(
        json.dumps(pending_codes, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def optimize_product_image(source_path: Path, product_code: str) -> str:
    PRODUCT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = PRODUCT_IMAGE_DIR / f"{product_code}.webp"

    with Image.open(source_path) as image:
        image = ImageOps.exif_transpose(image)
        image.thumbnail((900, 900), Image.Resampling.LANCZOS)
        if image.mode in ("RGBA", "LA"):
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))
            image = background
        else:
            image = image.convert("RGB")
        image.save(output_path, "WEBP", quality=82, method=6)

    return f"img/celulares/{product_code}.webp"


def update_product_image_mapping(product_code: str, image_path: str) -> str:
    mapping = {}
    if PRODUCT_IMAGES_JSON.exists():
        mapping = json.loads(PRODUCT_IMAGES_JSON.read_text(encoding="utf-8"))
    mapping[product_code] = image_path
    PRODUCT_IMAGES_JSON.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return "Imagen guardada localmente. Pendiente de publicar."


def copy_product_image(source_code: str, target_code: str) -> str:
    mapping = load_product_images()
    image_path = mapping.get(source_code)
    if not image_path:
        return f"No encontre imagen publicada o pendiente para el codigo origen {source_code}."
    if target_code not in product_codes():
        return f"No encontre el codigo destino {target_code} en el inventario actual."
    mapping[target_code] = image_path
    PRODUCT_IMAGES_JSON.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return f"Imagen copiada de {source_code} a {target_code}. Pendiente de publicar."


def similar_products_for_image(product_code: str, limit: int = 5) -> list[dict]:
    inventory = load_inventory()
    images = load_product_images()
    source_product = None
    for product in inventory.get("productos", []):
        if str(product.get("codigo", "")).strip() == product_code:
            source_product = product
            break
    if not source_product:
        return []

    source_key = compact_model_name(source_product.get("nombre", ""))
    suggestions = []
    for product in inventory.get("productos", []):
        code = str(product.get("codigo", "")).strip()
        if code == product_code or product_image(product, images):
            continue
        score = SequenceMatcher(None, source_key, compact_model_name(product.get("nombre", ""))).ratio()
        if score >= 0.82:
            suggestions.append((score, product))
    suggestions.sort(key=lambda item: item[0], reverse=True)
    return [product for _, product in suggestions[:limit]]


def similar_products_message(product_code: str) -> str:
    suggestions = similar_products_for_image(product_code)
    if not suggestions:
        return ""
    lines = [
        "",
        "Modelos parecidos sin imagen:",
        *[f"{product.get('codigo')} - {product.get('nombre')}" for product in suggestions],
        "",
        "Puedes reenviar la misma imagen con alguno de esos codigos si aplica.",
    ]
    return "\n".join(lines)


def pending_image_status() -> str:
    return run(["git", "status", "--short", "product-images.json", "img/celulares"])


def pending_image_count() -> int:
    status = pending_image_status()
    if not status:
        return 0
    return sum(1 for line in status.splitlines() if "img/celulares/" in line)


def publish_pending_images() -> str:
    status = pending_image_status()
    if not status:
        return "No hay imagenes pendientes por publicar."

    run(["git", "add", "product-images.json", "img/celulares"])
    staged = run(["git", "status", "--short", "product-images.json", "img/celulares"])
    if not staged:
        return "No hay imagenes pendientes por publicar."

    count = pending_image_count()
    run(["git", "commit", "-m", f"Publicar imagenes de productos ({count})"])
    run(["git", "pull", "--rebase", "origin", "main"])
    run(["git", "push", "origin", "main"])
    return f"Imagenes publicadas en GitHub ({count} cambios). Netlify publicara el lote automaticamente."


def read_auto_publish_state() -> dict:
    if not AUTO_PUBLISH_STATE_JSON.exists():
        return {}
    try:
        return json.loads(AUTO_PUBLISH_STATE_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_auto_publish_state(state: dict) -> None:
    AUTO_PUBLISH_STATE_JSON.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def should_auto_publish_images(now: Optional[datetime] = None) -> bool:
    if not AUTO_PUBLISH_ENABLED or not AUTO_PUBLISH_IMAGES_AT:
        return False
    now = now or datetime.now()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", AUTO_PUBLISH_IMAGES_AT)
    if not match:
        return False
    target_hour = int(match.group(1))
    target_minute = int(match.group(2))
    if now.hour != target_hour or now.minute != target_minute:
        return False
    state = read_auto_publish_state()
    return state.get("last_run_date") != now.date().isoformat()


async def maybe_auto_publish_images(bot: Bot) -> None:
    if not should_auto_publish_images():
        return
    state = read_auto_publish_state()
    state["last_run_date"] = datetime.now().date().isoformat()
    write_auto_publish_state(state)

    pending_count = await asyncio.to_thread(pending_image_count)
    if pending_count <= 0:
        return
    result = await asyncio.to_thread(publish_pending_images)
    message = (
        f"Publicacion automatica de imagenes ({AUTO_PUBLISH_IMAGES_AT}):\n"
        f"{result}\n\n"
        f"Liga solo celulares:\n{NETLIFY_SITE_URL.rstrip('/')}?celulares=1"
    )
    for chat_id in BROADCAST_CHAT_IDS:
        await safe_send(bot, chat_id, message)


def share_message(result: str, summary: str, report: str = "") -> str:
    report_block = f"\n\n{report}" if report else ""
    return (
        f"Listo: {summary}. {result}\n\n"
        f"{links_message()}"
        f"{report_block}"
    )


def append_google_sheets_result(message: str, sheets_result: str) -> str:
    if not sheets_result:
        return message
    return f"{message}\n\nDrive/Sheets: {sheets_result}"


def safe_sync_inventory_to_google_sheets(inventory: dict, list_key: str) -> str:
    try:
        return sync_inventory_to_google_sheets(inventory, list_key)
    except Exception as exc:
        return f"No pude actualizar Google Sheets: {exc}"


def links_message(prefix: str = "Ligas disponibles") -> str:
    normal_url = NETLIFY_SITE_URL
    dse_url = f"{NETLIFY_SITE_URL.rstrip('/')}?DSE=1"
    celulares_url = f"{NETLIFY_SITE_URL.rstrip('/')}?celulares=1"
    pl_url = f"{NETLIFY_SITE_URL.rstrip('/')}?PL=1"
    lista_g_url = f"{NETLIFY_SITE_URL.rstrip('/')}?listaG=1"
    payjoy_url = f"{NETLIFY_SITE_URL.rstrip('/')}?payjoy=1"
    return (
        f"{prefix}:\n\n"
        f"Liga catalogo completo:\n{normal_url}\n\n"
        f"Liga DSE:\n{dse_url}"
        f"\n\nLiga solo celulares:\n{celulares_url}"
        f"\n\nLiga PL:\n{pl_url}"
        f"\n\nLiga Lista G:\n{lista_g_url}"
        f"\n\nEquipos Payjoy - Payphone:\n{payjoy_url}"
    )


def error_message(exc: Exception) -> str:
    return (
        f"No pude actualizar el inventario: {exc}\n\n"
        f"{links_message('Ligas actuales')}"
    )


NETLIFY_CHECKS = (
    ("Lista M", "inventario.json", "productos"),
    ("Lista G", "listag.json", "productos"),
    ("Lista PL", "plus.json", "productos"),
    ("Payjoy - Payphone", "payjoy-payphone.json", "productos"),
    ("Imagenes", "product-images.json", None),
)


def read_netlify_monitor_state() -> dict:
    if not NETLIFY_MONITOR_STATE_JSON.exists():
        return {}
    try:
        return json.loads(NETLIFY_MONITOR_STATE_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_netlify_monitor_state(state: dict) -> None:
    NETLIFY_MONITOR_STATE_JSON.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def check_netlify_health() -> dict:
    results = []
    cache_buster = int(datetime.now().timestamp())
    for name, file_name, collection_key in NETLIFY_CHECKS:
        url = f"{NETLIFY_SITE_URL.rstrip('/')}/{file_name}?monitor={cache_buster}"
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "CelucenterBotMonitor/1.0"})
            with urllib.request.urlopen(request, timeout=25) as response:
                if response.status != 200:
                    raise ValueError(f"HTTP {response.status}")
                payload = json.loads(response.read().decode("utf-8"))
            collection = payload.get(collection_key, []) if collection_key else payload
            count = len(collection) if isinstance(collection, (list, dict)) else 0
            if count <= 0:
                raise ValueError("archivo vacio")
            results.append({"name": name, "ok": True, "count": count})
        except Exception as exc:
            results.append({"name": name, "ok": False, "count": 0, "error": str(exc)[:180]})
    return {
        "ok": all(item["ok"] for item in results),
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "results": results,
    }


def netlify_status_message(health: dict, title: str = "Estado del catalogo") -> str:
    lines = [f"{'✅' if health.get('ok') else '🚨'} {title}", f"Revision: {health.get('checked_at', 'ahora')}", ""]
    for item in health.get("results", []):
        if item.get("ok"):
            lines.append(f"✅ {item['name']}: {item.get('count', 0)}")
        else:
            lines.append(f"❌ {item['name']}: {item.get('error', 'no disponible')}")
    lines.append(f"\n🌐 {NETLIFY_SITE_URL}")
    return "\n".join(lines)


def force_netlify_redeploy() -> str:
    sync_from_github()
    run(["git", "commit", "--allow-empty", "-m", "Forzar republicacion del catalogo"])
    run(["git", "push", "origin", "main"])
    return "Republicacion enviada a Netlify. Puede tardar entre 1 y 3 minutos."


def monitor_notification_targets() -> list[str]:
    targets = list(BROADCAST_CHAT_IDS)
    if ALLOWED_CHAT_ID and ALLOWED_CHAT_ID not in targets:
        targets.append(ALLOWED_CHAT_ID)
    return targets


def is_admin_chat(chat_id: Union[str, int]) -> bool:
    configured = set(monitor_notification_targets())
    return not configured or str(chat_id) in configured


def operations_status_message() -> str:
    state = read_operations_state()
    lines = ["🛡️ Estado operativo remoto", ""]
    for key, path in LIST_FILES.items():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            count = len(data.get("productos", []))
            updated = state.get("last_updates", {}).get(key, {}).get("at") or datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
            lines.append(f"✅ {key}: {count} productos · {updated}")
        except Exception as exc:
            lines.append(f"❌ {key}: {exc}")
    image_count = len(load_product_images())
    lines.extend([
        f"✅ Imágenes registradas: {image_count}",
        f"✅ Monitor Netlify: cada {NETLIFY_MONITOR_INTERVAL_SECONDS // 60} min",
        f"✅ Protección: mínimo {INVENTORY_MIN_PRODUCTS} productos; caída máxima {INVENTORY_MAX_DROP_PERCENT}%",
        f"✅ Respaldos: {BACKUP_RETENTION} versiones por lista",
        f"Último respaldo diario: {state.get('last_daily_backup_at', 'pendiente')}",
    ])
    return "\n".join(lines)


def recent_errors_message(lines_count: int = 35) -> str:
    candidates = [ROOT / "telegram-bot.err.log", ROOT / "telegram-bot.log"]
    selected = []
    for path in candidates:
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in content[-500:]:
            if any(token in line.lower() for token in ("error", "traceback", "failed", "fatal", "exception")):
                selected.append(line.strip())
    if not selected:
        return "✅ No encontré errores recientes en los registros del bot."
    selected = selected[-lines_count:]
    message = "⚠️ Últimos errores registrados:\n\n" + "\n".join(selected)
    return message[-3800:]


def commands_message() -> str:
    return (
        "🛠️ Comandos de operación remota\n\n"
        "/estado — revisa Netlify y las cinco fuentes\n"
        "/operacion — resumen local, respaldos y protecciones\n"
        "/reparar — fuerza una republicación\n"
        "/restaurar M|G|PL|PAYJOY — recupera el respaldo anterior\n"
        "/reiniciar — reinicia este bot\n"
        "/ultimoserrores — muestra errores recientes\n"
        "/respaldo — crea un respaldo inmediato\n"
        "/ligas — muestra todas las ligas"
    )


async def maybe_monitor_netlify(bot: Bot, force: bool = False) -> Optional[dict]:
    if not NETLIFY_MONITOR_ENABLED and not force:
        return None
    state = read_netlify_monitor_state()
    now = datetime.now()
    last_check = state.get("last_check")
    if not force and last_check:
        try:
            elapsed = (now - datetime.fromisoformat(last_check)).total_seconds()
            if elapsed < NETLIFY_MONITOR_INTERVAL_SECONDS:
                return None
        except ValueError:
            pass

    health = await asyncio.to_thread(check_netlify_health)
    was_down = state.get("status") == "down"
    failures = 0 if health["ok"] else int(state.get("consecutive_failures", 0)) + 1
    state.update({
        "last_check": now.isoformat(timespec="seconds"),
        "last_health": health,
        "consecutive_failures": failures,
    })

    notification = None
    if not health["ok"] and failures >= 2 and not was_down:
        state["status"] = "down"
        repair_note = ""
        try:
            last_repair = state.get("last_auto_repair")
            can_repair = True
            if last_repair:
                can_repair = (now - datetime.fromisoformat(last_repair)).total_seconds() >= 3600
            if can_repair:
                repair_result = await asyncio.to_thread(force_netlify_redeploy)
                state["last_auto_repair"] = now.isoformat(timespec="seconds")
                repair_note = f"\n\n🔄 Reparación automática iniciada: {repair_result}"
            else:
                repair_note = "\n\nLa reparación automática ya se ejecutó durante la última hora."
        except Exception as exc:
            repair_note = f"\n\n❌ La reparación automática no pudo iniciarse: {exc}\nUsa /reparar."
        notification = netlify_status_message(health, "Falla confirmada en las listas") + repair_note
    elif health["ok"] and was_down:
        state["status"] = "ok"
        notification = netlify_status_message(health, "Listas recuperadas")
    elif health["ok"]:
        state["status"] = "ok"
    write_netlify_monitor_state(state)

    if notification:
        for target in monitor_notification_targets():
            await safe_send(bot, target, notification)
    return health


def is_plus_pdf(file_name: str) -> bool:
    clean = file_name.lower()
    return "plus" in clean or clean.startswith("pl")


def is_lista_g_pdf(file_name: str, inventory: Optional[dict] = None) -> bool:
    clean = re.sub(r"[^a-z0-9]+", "", file_name.lower())
    if "listag" in clean:
        return True
    title = str((inventory or {}).get("report_title", ""))
    lista = str((inventory or {}).get("lista", ""))
    return "LISTA G" in title.upper() or lista.upper() == "G"


def requested_pdf_list(caption: str) -> Optional[str]:
    """Allow an explicit list type when a supplier sends generic numeric filenames."""
    clean = re.sub(r"[^a-z0-9]+", "", (caption or "").lower())
    aliases = {
        "listam": "M", "m": "M",
        "listag": "G", "g": "G",
        "listapl": "PL", "pl": "PL", "plus": "PL", "listaplus": "PL",
    }
    return aliases.get(clean)


def classify_and_extract_pdf(pdf_path: Path, file_name: str, caption: str = "") -> tuple[str, dict]:
    """Classify PDFs by their contents, using filename/caption only as useful hints."""
    forced = requested_pdf_list(caption)
    if forced == "PL" or (not forced and is_plus_pdf(file_name)):
        inventory = extract_plus(pdf_path, INVENTORY_JSON)
        return "PL", inventory

    regular = extract_inventory(pdf_path)
    regular_count = len(regular.get("productos", []))
    if forced in {"M", "G"}:
        regular["lista"] = forced
        return forced, regular
    if regular_count >= INVENTORY_MIN_PRODUCTS:
        list_type = "G" if is_lista_g_pdf(file_name, regular) else "M"
        regular["lista"] = list_type
        return list_type, regular

    # PL reports use a different layout and often arrive with numeric filenames.
    plus = extract_plus(pdf_path, INVENTORY_JSON)
    if len(plus.get("productos", [])) >= INVENTORY_MIN_PRODUCTS:
        return "PL", plus
    raise ValueError(
        "No pude identificar el PDF como Lista M, Lista G o Lista PL. "
        "Reenvialo con el texto /lista_m, /lista_g o /lista_pl en el caption."
    )


async def safe_send(bot: Bot, chat_id: Union[str, int], text: str) -> bool:
    for attempt in range(1, 4):
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                read_timeout=TELEGRAM_TIMEOUT_SECONDS,
                write_timeout=TELEGRAM_TIMEOUT_SECONDS,
                connect_timeout=TELEGRAM_TIMEOUT_SECONDS,
                pool_timeout=TELEGRAM_TIMEOUT_SECONDS,
            )
            return True
        except TelegramError as exc:
            print(f"No pude enviar mensaje a Telegram intento {attempt}/3: {exc}")
            await asyncio.sleep(2 * attempt)
    return False


async def download_telegram_file_with_retries(bot: Bot, file_id: str, destination: Path, attempts: int = 3) -> None:
    last_error: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            telegram_file = await bot.get_file(
                file_id,
                read_timeout=TELEGRAM_TIMEOUT_SECONDS,
                write_timeout=TELEGRAM_TIMEOUT_SECONDS,
                connect_timeout=TELEGRAM_TIMEOUT_SECONDS,
                pool_timeout=TELEGRAM_TIMEOUT_SECONDS,
            )
            await telegram_file.download_to_drive(
                custom_path=destination,
                read_timeout=TELEGRAM_MEDIA_TIMEOUT_SECONDS,
                write_timeout=TELEGRAM_MEDIA_TIMEOUT_SECONDS,
                connect_timeout=TELEGRAM_TIMEOUT_SECONDS,
                pool_timeout=TELEGRAM_TIMEOUT_SECONDS,
            )
            return
        except (TimedOut, NetworkError) as exc:
            last_error = exc
            print(f"Descarga Telegram intento {attempt}/{attempts} fallo: {exc}")
            await asyncio.sleep(3 * attempt)
    raise last_error or TimedOut("No pude descargar el archivo desde Telegram.")


async def broadcast_inventory_update(bot: Bot, source_chat_id: str, message: str) -> None:
    for target_chat_id in BROADCAST_CHAT_IDS:
        if target_chat_id == source_chat_id:
            continue
        await safe_send(bot, target_chat_id, message)


async def handle_payjoy_excel(bot: Bot, update: Update) -> bool:
    if not update.message or not update.message.document:
        return False
    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    if ALLOWED_CHAT_ID and chat_id != ALLOWED_CHAT_ID:
        return True

    document = update.message.document
    file_name = document.file_name or "equipos-payjoy.xlsb"
    if not file_name.lower().endswith(".xlsb"):
        return False

    await safe_send(bot, chat_id, f"Recibi Excel Payjoy - Payphone: {file_name}. Actualizando lista...")
    with tempfile.TemporaryDirectory() as temp_dir:
        excel_path = Path(temp_dir) / file_name
        try:
            await download_telegram_file_with_retries(bot, document.file_id, excel_path)
            await asyncio.to_thread(sync_from_github)
            inventory = await asyncio.to_thread(
                extract_payjoy_excel,
                excel_path,
                [INVENTORY_JSON, LISTA_G_JSON, PLUS_JSON],
                PRODUCT_IMAGES_JSON,
            )
            summary = await asyncio.to_thread(write_payjoy_data, inventory)
            result = await asyncio.to_thread(publish_payjoy_to_github, summary)
            message = (
                f"Listo: {summary}. {result}\n\n"
                f"Liga Equipos Payjoy - Payphone:\n{NETLIFY_SITE_URL.rstrip('/')}?payjoy=1"
            )
            await safe_send(bot, chat_id, message)
            await broadcast_inventory_update(bot, chat_id, message)
        except Exception as exc:
            await safe_send(bot, chat_id, f"No pude actualizar Equipos Payjoy - Payphone: {exc}")
            raise
    return True


async def handle_pdf(bot: Bot, update: Update) -> None:
    if not update.message or not update.message.document:
        return

    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    if ALLOWED_CHAT_ID and chat_id != ALLOWED_CHAT_ID:
        return

    document = update.message.document
    file_name = document.file_name or "inventario.pdf"
    caption = update.message.caption or ""
    if not file_name.lower().endswith(".pdf"):
        return
    if PDF_NAME_CONTAINS and PDF_NAME_CONTAINS not in file_name.lower():
        return

    await safe_send(bot, chat_id, f"Recibi PDF: {file_name}. Revisando si es Lista M, G o PL...")
    print(f"PDF recibido chat={chat_id} archivo={file_name!r} caption={caption!r}")

    with tempfile.TemporaryDirectory() as temp_dir:
        pdf_path = Path(temp_dir) / file_name
        try:
            await download_telegram_file_with_retries(bot, document.file_id, pdf_path)
            await asyncio.to_thread(sync_from_github)
            list_type, parsed_inventory = await asyncio.to_thread(
                classify_and_extract_pdf, pdf_path, file_name, caption
            )
            product_count = len(parsed_inventory.get("productos", []))
            print(f"PDF clasificado archivo={file_name!r} lista={list_type} productos={product_count}")
            await safe_send(bot, chat_id, f"Detectada Lista {list_type}: {product_count} productos. Publicando...")
            if list_type == "PL":
                summary = await asyncio.to_thread(write_plus_data, parsed_inventory)
                result = await asyncio.to_thread(publish_plus_to_github, summary)
                sheets_result = await asyncio.to_thread(safe_sync_inventory_to_google_sheets, parsed_inventory, "PL")
                message = (
                    f"Listo: {summary}. {result}\n\n"
                    f"Liga PL:\n{NETLIFY_SITE_URL.rstrip('/')}?PL=1"
                )
                message = append_google_sheets_result(message, sheets_result)
            else:
                if list_type == "G":
                    summary = await asyncio.to_thread(write_lista_g_data, parsed_inventory)
                    result = await asyncio.to_thread(publish_lista_g_to_github, summary)
                    sheets_result = await asyncio.to_thread(safe_sync_inventory_to_google_sheets, parsed_inventory, "G")
                    message = (
                        f"Listo: {summary}. {result}\n\n"
                        f"Liga Lista G:\n{NETLIFY_SITE_URL.rstrip('/')}?listaG=1"
                    )
                    message = append_google_sheets_result(message, sheets_result)
                else:
                    new_inventory = parsed_inventory
                    previous_inventory = await asyncio.to_thread(load_inventory)
                    analysis = await asyncio.to_thread(analyze_inventory_update, previous_inventory, new_inventory)
                    report = await asyncio.to_thread(build_update_report, previous_inventory, new_inventory)
                    summary = await asyncio.to_thread(write_inventory_data, new_inventory)
                    await asyncio.to_thread(save_update_report_state, analysis, summary)
                    result = await asyncio.to_thread(publish_to_github, summary)
                    sheets_result = await asyncio.to_thread(safe_sync_inventory_to_google_sheets, new_inventory, "M")
                    message = share_message(result, summary, report)
                    message = append_google_sheets_result(message, sheets_result)
            await safe_send(bot, chat_id, message)
            await broadcast_inventory_update(bot, chat_id, message)
        except Exception as exc:
            print(f"PDF rechazado archivo={file_name!r}: {type(exc).__name__}: {exc}")
            await safe_send(bot, chat_id, error_message(exc))
            raise


async def handle_product_image(bot: Bot, update: Update) -> bool:
    if not update.message:
        return False

    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    if ALLOWED_CHAT_ID and chat_id != ALLOWED_CHAT_ID:
        return True

    document = update.message.document
    has_image_document = bool(document and (document.mime_type or "").startswith("image/"))
    has_photo = bool(update.message.photo)
    if not has_photo and not has_image_document:
        return False

    caption = update.message.caption or ""
    product_code = extract_product_code(caption)
    used_pending_code = False
    if not product_code:
        product_code = get_pending_image_code(chat_id)
        used_pending_code = bool(product_code)
    if not product_code:
        await safe_send(
            bot,
            chat_id,
            (
                "Recibi la imagen, pero falta el codigo.\n\n"
                "Puedes reenviar primero el texto donde aparece CODIGO: y despues la imagen, "
                "o enviar la imagen con caption asi:\n"
                "codigo 848958043799"
            ),
        )
        return True

    source_note = " del mensaje anterior" if used_pending_code else ""
    await safe_send(bot, chat_id, f"Recibi imagen para codigo {product_code}{source_note}. Actualizando...")

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir) / "producto"
        if has_photo:
            file_id = update.message.photo[-1].file_id
            temp_path = temp_path.with_suffix(".jpg")
        else:
            file_id = document.file_id
            suffix = Path(document.file_name or "").suffix or ".jpg"
            temp_path = temp_path.with_suffix(suffix)

        try:
            await safe_send(bot, chat_id, "Descargando imagen desde Telegram...")
            await download_telegram_file_with_retries(bot, file_id, temp_path)

            await safe_send(bot, chat_id, "Preparando imagen y guardandola localmente...")
            exists_in_inventory = product_code in await asyncio.to_thread(product_codes)
            image_path = await asyncio.to_thread(optimize_product_image, temp_path, product_code)

            result = await asyncio.to_thread(update_product_image_mapping, product_code, image_path)
            pending_count = await asyncio.to_thread(pending_image_count)
            if used_pending_code:
                clear_pending_image_code(chat_id)

            warning = "" if exists_in_inventory else "\n\nOjo: no encontre ese codigo en el inventario actual, pero deje la imagen guardada para cuando aparezca."
            similar_message = await asyncio.to_thread(similar_products_message, product_code)
            await safe_send(
                bot,
                chat_id,
                (
                    f"Listo. Codigo {product_code}: {result}{warning}\n\n"
                    f"Imagenes pendientes por publicar: {pending_count}\n"
                    "Cuando termines de cargar imagenes, manda /publicarimagenes."
                    f"{similar_message}"
                ),
            )
        except Exception as exc:
            await safe_send(
                bot,
                chat_id,
                (
                    f"No pude actualizar la imagen del codigo {product_code}: {exc}\n\n"
                    f"{links_message('Ligas actuales')}"
                ),
            )
            raise

    return True


def advertisement_features(update: Update) -> Optional[str]:
    if not update.message:
        return None
    caption = (update.message.caption or "").strip()
    if not caption.lower().startswith("/publicidad"):
        return None
    return caption[len("/publicidad"):].strip()


async def handle_advertisement_image(bot: Bot, update: Update) -> bool:
    if not update.message or not update.effective_chat:
        return False
    chat_id = str(update.effective_chat.id)
    caption_features = advertisement_features(update)
    pending = chat_id in PENDING_ADVERTISEMENT_CHATS
    if caption_features is None and not pending:
        return False

    document = update.message.document
    has_image_document = bool(document and (document.mime_type or "").startswith("image/"))
    has_photo = bool(update.message.photo)
    if not has_photo and not has_image_document:
        return False
    features = caption_features or (update.message.caption or "").strip()
    if not features:
        await safe_send(
            bot,
            chat_id,
            "Faltan las caracteristicas. Envia la foto con un pie como: /publicidad NOMBRE DEL PRODUCTO y sus caracteristicas.",
        )
        return True
    if not OPENAI_API_KEY:
        await safe_send(bot, chat_id, "La generacion de publicidad aun no tiene configurada la clave de OpenAI.")
        return True
    try:
        from advertising import generate_advertisement
    except ImportError as exc:
        await safe_send(bot, chat_id, f"No puedo generar publicidad porque falta una dependencia: {exc}")
        return True

    PENDING_ADVERTISEMENT_CHATS.discard(chat_id)
    await safe_send(bot, chat_id, "Creando una publicidad nueva. Puede tardar hasta 2 minutos...")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        reference_path = temp_root / ("referencia.jpg" if has_photo else (document.file_name or "referencia.png"))
        output_path = temp_root / "publicidad.jpg"
        file_id = update.message.photo[-1].file_id if has_photo else document.file_id
        try:
            await download_telegram_file_with_retries(bot, file_id, reference_path)
            style = await asyncio.to_thread(generate_advertisement, reference_path, features, output_path)
            with output_path.open("rb") as image_file:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=image_file,
                    caption=f"Publicidad lista - estilo {style}.\n\nPara otra version, reenvia la foto con /publicidad y las caracteristicas.",
                    read_timeout=TELEGRAM_TIMEOUT_SECONDS,
                    write_timeout=TELEGRAM_TIMEOUT_SECONDS,
                    connect_timeout=TELEGRAM_TIMEOUT_SECONDS,
                    pool_timeout=TELEGRAM_TIMEOUT_SECONDS,
                )
        except Exception as exc:
            print(f"Error generando publicidad: {type(exc).__name__}: {exc}")
            await safe_send(
                bot,
                chat_id,
                "No pude generar la publicidad. Revisa que la cuenta de OpenAI tenga saldo y vuelve a intentarlo.",
            )
        return True


async def handle_message(bot: Bot, update: Update) -> None:
    if not update.message or not update.effective_chat:
        return
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()
    key = command_key(text)
    if key == "/start":
        await safe_send(
            bot,
            chat_id,
            "Listo. Reenvíame el PDF de inventario para actualizar existencias. Para crear un anuncio usa /publicidad y luego envia la foto con las caracteristicas.",
        )
        return
    if key == "/publicidad":
        PENDING_ADVERTISEMENT_CHATS.add(str(chat_id))
        await safe_send(bot, chat_id, "Envia ahora una foto del producto. En el pie escribe el nombre y todas las caracteristicas que deben aparecer.")
        return
    if key == "/id":
        await safe_send(bot, chat_id, f"Chat ID: {chat_id}")
        return
    if key in {"/estado", "estado"}:
        await safe_send(bot, chat_id, "Revisando Netlify y todas las listas...")
        health = await asyncio.to_thread(check_netlify_health)
        await safe_send(bot, chat_id, netlify_status_message(health) + "\n\n" + operations_status_message())
        return
    if key in {"/operacion", "/operación", "operacion", "operación"}:
        await safe_send(bot, chat_id, operations_status_message())
        return
    if key in {"/comandos", "comandos", "/ayudaoperacion"}:
        await safe_send(bot, chat_id, commands_message())
        return
    if key in {"/reparar", "reparar"}:
        if not is_admin_chat(chat_id):
            await safe_send(bot, chat_id, "Este comando está limitado a los chats administrativos.")
            return
        await safe_send(bot, chat_id, "Forzando una nueva publicacion del ultimo catalogo valido...")
        try:
            result = await asyncio.to_thread(force_netlify_redeploy)
            state = read_netlify_monitor_state()
            state["last_check"] = ""
            write_netlify_monitor_state(state)
            await safe_send(bot, chat_id, f"✅ {result}\n\nConsulta /estado dentro de unos minutos.")
        except Exception as exc:
            await safe_send(bot, chat_id, f"❌ No pude iniciar la reparacion: {exc}")
            raise
        return
    if key.startswith("/restaurar") or key.startswith("restaurar"):
        if not is_admin_chat(chat_id):
            await safe_send(bot, chat_id, "Este comando está limitado a los chats administrativos.")
            return
        parts = text.split(maxsplit=1)
        list_key = parts[1] if len(parts) > 1 else ""
        await safe_send(bot, chat_id, f"Restaurando la última versión respaldada de {list_key.upper() or 'la lista indicada'}...")
        try:
            result = await asyncio.to_thread(restore_latest_backup, list_key)
            await safe_send(bot, chat_id, f"✅ {result}\n\nRevisa /estado dentro de unos minutos.")
        except Exception as exc:
            await safe_send(bot, chat_id, f"❌ No pude restaurar: {exc}")
        return
    if key in {"/respaldo", "respaldo"}:
        if not is_admin_chat(chat_id):
            await safe_send(bot, chat_id, "Este comando está limitado a los chats administrativos.")
            return
        for list_key, path in LIST_FILES.items():
            await asyncio.to_thread(backup_file, path, list_key)
        await asyncio.to_thread(backup_file, PRODUCT_IMAGES_JSON, "IMAGES")
        state = read_operations_state()
        state["last_manual_backup_at"] = datetime.now().isoformat(timespec="seconds")
        write_operations_state(state)
        await safe_send(bot, chat_id, "✅ Respaldo inmediato creado para M, G, PL, Payjoy e imágenes.")
        return
    if key in {"/ultimoserrores", "/errores", "ultimos errores"}:
        await safe_send(bot, chat_id, recent_errors_message())
        return
    if key in {"/reiniciar", "reiniciar"}:
        if not is_admin_chat(chat_id):
            await safe_send(bot, chat_id, "Este comando está limitado a los chats administrativos.")
            return
        await safe_send(bot, chat_id, "🔄 Reiniciando el bot. Volverá automáticamente en unos segundos.")
        asyncio.get_running_loop().call_later(1.0, lambda: os._exit(0))
        return
    if key in {"/ligas", "ligas", "dame las ligas", "dame las ligas de las listas"}:
        await safe_send(bot, chat_id, links_message())
        return
    if key in {"/whatsapp", "whatsapp", "texto whatsapp", "liga whatsapp"}:
        await safe_send(bot, chat_id, whatsapp_message())
        return
    if key in {"/whatsapppayjoy", "/payjoywhatsapp", "whatsapp payjoy", "texto payjoy"}:
        for message in payjoy_whatsapp_messages():
            await safe_send(bot, chat_id, message)
        return
    if key in {"/drive", "/sheets", "drive", "google sheets"}:
        await safe_send(bot, chat_id, google_sheets_status_message())
        return
    if key.startswith("/producto ") or key.startswith("producto "):
        query = text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else ""
        await safe_send(bot, chat_id, product_message(query))
        return
    if key in {"/nuevos", "nuevos", "productos nuevos"}:
        await safe_send(bot, chat_id, latest_new_products_message())
        return
    if key in {"/oportunidades", "/oportunidad", "oportunidades", "lista de oportunidad"}:
        await safe_send(bot, chat_id, opportunities_message())
        return
    if key in {"/alertas", "alertas", "cambios importantes"}:
        state = load_update_report_state()
        alerts = state.get("important_price_changes", []) if state else []
        if not alerts:
            await safe_send(bot, chat_id, "No tengo alertas importantes guardadas del ultimo inventario.")
        else:
            lines = ["Alertas importantes del ultimo inventario:"]
            for alert in alerts[:25]:
                product = alert.get("producto", {})
                lines.append(
                    f"{product.get('codigo')} - {product.get('nombre')} - "
                    f"${alert.get('precio_anterior', 0):,.2f} -> ${alert.get('precio_nuevo', 0):,.2f}"
                )
            await safe_send(bot, chat_id, "\n".join(lines))
        return
    if key in {"/autopublicacion", "/autoimagenes", "auto imagenes"}:
        status = "activa" if AUTO_PUBLISH_ENABLED else "apagada"
        await safe_send(
            bot,
            chat_id,
            f"Publicacion automatica de imagenes: {status}\nHora configurada: {AUTO_PUBLISH_IMAGES_AT}",
        )
        return
    if key.startswith("/copiarimagen ") or key.startswith("copiar imagen "):
        parts = text.split()
        if key.startswith("copiar imagen "):
            source_target = parts[-2:] if len(parts) >= 4 else []
        else:
            source_target = parts[1:3]
        if len(source_target) < 2:
            await safe_send(bot, chat_id, "Uso: /copiarimagen CODIGO_ORIGEN CODIGO_DESTINO")
        else:
            result = await asyncio.to_thread(copy_product_image, source_target[0].strip(), source_target[1].strip())
            pending_count = await asyncio.to_thread(pending_image_count)
            await safe_send(
                bot,
                chat_id,
                f"{result}\nImagenes pendientes por publicar: {pending_count}\nManda /publicarimagenes cuando quieras actualizar Netlify.",
            )
        return
    if key in {"/sinimagenes", "/sin_imagenes", "sin imagenes", "celulares sin imagen"}:
        await safe_send(bot, chat_id, missing_images_message())
        return
    if key in {"/imagenespendientes", "/imagenes_pendientes", "imagenes pendientes"}:
        pending_count = await asyncio.to_thread(pending_image_count)
        await safe_send(bot, chat_id, f"Imagenes pendientes por publicar: {pending_count}")
        return
    if key in {"/publicarimagenes", "/publicar_imagenes", "publicar imagenes"}:
        await safe_send(bot, chat_id, "Publicando imagenes pendientes en GitHub...")
        try:
            result = await asyncio.to_thread(publish_pending_images)
            await safe_send(
                bot,
                chat_id,
                (
                    f"{result}\n\n"
                    f"Liga solo celulares:\n{NETLIFY_SITE_URL.rstrip('/')}?celulares=1"
                ),
            )
        except Exception as exc:
            await safe_send(
                bot,
                chat_id,
                (
                    f"No pude publicar las imagenes pendientes: {exc}\n\n"
                    "Las imagenes siguen guardadas localmente. Puedes intentar otra vez con /publicarimagenes."
                ),
            )
            raise
        return
    if await handle_advertisement_image(bot, update):
        return
    if await handle_product_image(bot, update):
        return
    pending_product_code = extract_product_code(text)
    if pending_product_code:
        set_pending_image_code(str(chat_id), pending_product_code)
        await safe_send(
            bot,
            chat_id,
            (
                f"Codigo detectado: {pending_product_code}.\n"
                "Ahora reenvia la imagen y la guardare con ese codigo."
            ),
        )
        return
    if await handle_payjoy_excel(bot, update):
        return
    await handle_pdf(bot, update)


async def poll(token: str) -> None:
    request = HTTPXRequest(
        read_timeout=45,
        write_timeout=30,
        connect_timeout=30,
        pool_timeout=5,
        media_write_timeout=TELEGRAM_TIMEOUT_SECONDS,
    )
    bot = Bot(token, request=request)
    while True:
        try:
            me = await bot.get_me()
            break
        except RetryAfter as exc:
            delay = int(getattr(exc, "retry_after", 5) or 5)
            print(f"Telegram get_me flood control: esperando {delay}s")
            await asyncio.sleep(delay + 1)
        except Conflict as exc:
            print(f"Telegram get_me conflict: {exc}")
            await asyncio.sleep(10)
        except (TimedOut, NetworkError) as exc:
            print(f"Telegram get_me retry: {exc}")
            await asyncio.sleep(3)
    print(f"Bot activo: @{me.username}")
    offset = None

    while True:
        try:
            updates = await bot.get_updates(offset=offset, timeout=30, allowed_updates=["message"])
        except RetryAfter as exc:
            delay = int(getattr(exc, "retry_after", 5) or 5)
            print(f"Telegram polling flood control: esperando {delay}s")
            await asyncio.sleep(delay + 1)
            continue
        except Conflict as exc:
            print(f"Telegram polling conflict: {exc}")
            await asyncio.sleep(10)
            continue
        except (TimedOut, NetworkError) as exc:
            print(f"Telegram polling retry: {exc}")
            try:
                await maybe_auto_publish_images(bot)
            except Exception as auto_exc:
                print(f"Error en publicacion automatica de imagenes: {auto_exc}")
            try:
                await maybe_monitor_netlify(bot)
            except Exception as monitor_exc:
                print(f"Error en monitor de Netlify: {monitor_exc}")
            await asyncio.sleep(2)
            continue

        for update in updates:
            offset = update.update_id + 1
            try:
                await handle_message(bot, update)
            except Exception as exc:
                print(f"Error procesando update {update.update_id}: {exc}")
                traceback.print_exc()
        try:
            await maybe_auto_publish_images(bot)
        except Exception as exc:
            print(f"Error en publicacion automatica de imagenes: {exc}")
        try:
            await maybe_monitor_netlify(bot)
        except Exception as exc:
            print(f"Error en monitor de Netlify: {exc}")
        try:
            await asyncio.to_thread(daily_backup_if_needed)
        except Exception as exc:
            print(f"Error en respaldo diario: {exc}")
        await asyncio.sleep(0.2)


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Falta TELEGRAM_BOT_TOKEN en variables de entorno.")
    try:
        asyncio.run(poll(token))
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
