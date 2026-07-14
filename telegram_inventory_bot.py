#!/usr/bin/env python3
import asyncio
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Union

from telegram import Bot, Update
from telegram.error import NetworkError, TelegramError, TimedOut
from telegram.request import HTTPXRequest
from PIL import Image, ImageOps

from parse_inventory_pdf import extract_inventory


ROOT = Path(__file__).resolve().parent
INVENTORY_JSON = ROOT / "inventario.json"
PRODUCT_IMAGES_JSON = ROOT / "product-images.json"
PRODUCT_IMAGE_DIR = ROOT / "img" / "celulares"
PENDING_IMAGE_STATE_JSON = ROOT / ".telegram-image-state.json"
PDF_NAME_CONTAINS = os.getenv("PDF_NAME_CONTAINS", "").lower().strip()
ALLOWED_CHAT_ID = os.getenv("ALLOWED_CHAT_ID", "").strip()
BROADCAST_CHAT_IDS = [
    chat_id.strip()
    for chat_id in os.getenv("BROADCAST_CHAT_IDS", "").replace(";", ",").split(",")
    if chat_id.strip()
]
NETLIFY_SITE_URL = os.getenv("NETLIFY_SITE_URL", "https://listadeexistenciasdiario.netlify.app/").strip()
TELEGRAM_TIMEOUT_SECONDS = 120
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


def inventory_summary(inventory: dict) -> str:
    return (
        f"{inventory['total_productos']} productos, "
        f"{inventory['total_disponibles']} disponibles, "
        f"{inventory['total_agotados']} agotados"
    )


def write_inventory_data(inventory: dict) -> str:
    INVENTORY_JSON.write_text(
        __import__("json").dumps(inventory, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return inventory_summary(inventory)


def sync_from_github() -> None:
    # If a previous run failed after writing inventario.json, discard that
    # generated file before pulling. The current PDF will regenerate it.
    run(["git", "restore", "--", "inventario.json"])
    run(["git", "pull", "--rebase", "origin", "main"])


def publish_to_github(summary: str) -> str:
    status = run(["git", "status", "--short", "inventario.json"])
    if not status:
        return "El inventario no tuvo cambios."

    run(["git", "add", "inventario.json"])
    run(["git", "commit", "-m", f"Actualizar inventario diario ({summary})"])
    run(["git", "push", "origin", "main"])
    return "Inventario actualizado en GitHub. Netlify publicara el cambio automaticamente."


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


def inventory_by_code(inventory: dict) -> dict[str, dict]:
    return {
        str(product.get("codigo", "")).strip(): product
        for product in inventory.get("productos", [])
        if product.get("codigo")
    }


def price_value(product: dict) -> float:
    return float(product.get("precio_lista_m", product.get("precio", 0)) or 0)


def build_update_report(previous_inventory: dict, new_inventory: dict, limit: int = 12) -> str:
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

    lines = [
        "Reporte de actualización:",
        f"Productos nuevos: {len(new_products)}",
        f"Cambios de precio: {len(price_changes)}",
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

    return "\n".join(lines)


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

    run(["git", "add", image_path, "product-images.json"])
    status = run(["git", "status", "--short", image_path, "product-images.json"])
    if not status:
        return "La imagen ya estaba actualizada."

    run(["git", "commit", "-m", f"Agregar imagen de producto {product_code}"])
    run(["git", "push", "origin", "main"])
    return "Imagen actualizada en GitHub. Netlify publicara el cambio automaticamente."


def share_message(result: str, summary: str, report: str = "") -> str:
    report_block = f"\n\n{report}" if report else ""
    return (
        f"Listo: {summary}. {result}\n\n"
        f"{links_message()}"
        f"{report_block}"
    )


def links_message(prefix: str = "Ligas disponibles") -> str:
    normal_url = NETLIFY_SITE_URL
    dse_url = f"{NETLIFY_SITE_URL.rstrip('/')}?DSE=1"
    celulares_url = f"{NETLIFY_SITE_URL.rstrip('/')}?celulares=1"
    return (
        f"{prefix}:\n\n"
        f"Liga catalogo completo:\n{normal_url}\n\n"
        f"Liga DSE:\n{dse_url}"
        f"\n\nLiga solo celulares:\n{celulares_url}"
    )


def error_message(exc: Exception) -> str:
    return (
        f"No pude actualizar el inventario: {exc}\n\n"
        f"{links_message('Ligas actuales')}"
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


async def broadcast_inventory_update(bot: Bot, source_chat_id: str, message: str) -> None:
    for target_chat_id in BROADCAST_CHAT_IDS:
        if target_chat_id == source_chat_id:
            continue
        await safe_send(bot, target_chat_id, message)


async def handle_pdf(bot: Bot, update: Update) -> None:
    if not update.message or not update.message.document:
        return

    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    if ALLOWED_CHAT_ID and chat_id != ALLOWED_CHAT_ID:
        return

    document = update.message.document
    file_name = document.file_name or "inventario.pdf"
    if not file_name.lower().endswith(".pdf"):
        return
    if PDF_NAME_CONTAINS and PDF_NAME_CONTAINS not in file_name.lower():
        return

    await safe_send(bot, chat_id, f"Recibi PDF: {file_name}. Actualizando inventario...")

    with tempfile.TemporaryDirectory() as temp_dir:
        pdf_path = Path(temp_dir) / file_name
        try:
            telegram_file = await bot.get_file(document.file_id)
            await telegram_file.download_to_drive(
                custom_path=pdf_path,
                read_timeout=TELEGRAM_TIMEOUT_SECONDS,
                write_timeout=TELEGRAM_TIMEOUT_SECONDS,
                connect_timeout=TELEGRAM_TIMEOUT_SECONDS,
                pool_timeout=TELEGRAM_TIMEOUT_SECONDS,
            )
            await asyncio.to_thread(sync_from_github)
            previous_inventory = await asyncio.to_thread(load_inventory)
            new_inventory = await asyncio.to_thread(extract_inventory, pdf_path)
            report = await asyncio.to_thread(build_update_report, previous_inventory, new_inventory)
            summary = await asyncio.to_thread(write_inventory_data, new_inventory)
            result = await asyncio.to_thread(publish_to_github, summary)
            message = share_message(result, summary, report)
            await safe_send(bot, chat_id, message)
            await broadcast_inventory_update(bot, chat_id, message)
        except Exception as exc:
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
            telegram_file = await bot.get_file(file_id)
            await telegram_file.download_to_drive(
                custom_path=temp_path,
                read_timeout=TELEGRAM_TIMEOUT_SECONDS,
                write_timeout=TELEGRAM_TIMEOUT_SECONDS,
                connect_timeout=TELEGRAM_TIMEOUT_SECONDS,
                pool_timeout=TELEGRAM_TIMEOUT_SECONDS,
            )

            await safe_send(bot, chat_id, "Sincronizando catalogo y preparando imagen...")
            await asyncio.to_thread(sync_from_github)
            exists_in_inventory = product_code in await asyncio.to_thread(product_codes)
            image_path = await asyncio.to_thread(optimize_product_image, temp_path, product_code)

            await safe_send(bot, chat_id, "Subiendo imagen a GitHub para que Netlify la publique...")
            result = await asyncio.to_thread(update_product_image_mapping, product_code, image_path)
            if used_pending_code:
                clear_pending_image_code(chat_id)

            warning = "" if exists_in_inventory else "\n\nOjo: no encontre ese codigo en el inventario actual, pero deje la imagen guardada para cuando aparezca."
            await safe_send(
                bot,
                chat_id,
                (
                    f"Listo. Codigo {product_code}: {result}{warning}\n\n"
                    f"Liga solo celulares:\n{NETLIFY_SITE_URL.rstrip('/')}?celulares=1"
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


async def handle_message(bot: Bot, update: Update) -> None:
    if not update.message or not update.effective_chat:
        return
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()
    key = command_key(text)
    if key == "/start":
        await safe_send(bot, chat_id, "Listo. Reenvíame el PDF de inventario y actualizaré la lista de existencias.")
        return
    if key == "/id":
        await safe_send(bot, chat_id, f"Chat ID: {chat_id}")
        return
    if key in {"/ligas", "ligas", "dame las ligas", "dame las ligas de las listas"}:
        await safe_send(bot, chat_id, links_message())
        return
    if key in {"/whatsapp", "whatsapp", "texto whatsapp", "liga whatsapp"}:
        await safe_send(bot, chat_id, whatsapp_message())
        return
    if key in {"/sinimagenes", "/sin_imagenes", "sin imagenes", "celulares sin imagen"}:
        await safe_send(bot, chat_id, missing_images_message())
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
        except (TimedOut, NetworkError) as exc:
            print(f"Telegram get_me retry: {exc}")
            await asyncio.sleep(3)
    print(f"Bot activo: @{me.username}")
    offset = None

    while True:
        try:
            updates = await bot.get_updates(offset=offset, timeout=30, allowed_updates=["message"])
        except (TimedOut, NetworkError) as exc:
            print(f"Telegram polling retry: {exc}")
            await asyncio.sleep(2)
            continue

        for update in updates:
            offset = update.update_id + 1
            try:
                await handle_message(bot, update)
            except Exception as exc:
                print(f"Error procesando update {update.update_id}: {exc}")
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
