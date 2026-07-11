#!/usr/bin/env python3
import asyncio
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from telegram import Bot, Update
from telegram.error import NetworkError, TimedOut
from PIL import Image, ImageOps

from parse_inventory_pdf import extract_inventory


ROOT = Path(__file__).resolve().parent
INVENTORY_JSON = ROOT / "inventario.json"
PRODUCT_IMAGES_JSON = ROOT / "product-images.json"
PRODUCT_IMAGE_DIR = ROOT / "img" / "celulares"
PDF_NAME_CONTAINS = os.getenv("PDF_NAME_CONTAINS", "").lower().strip()
ALLOWED_CHAT_ID = os.getenv("ALLOWED_CHAT_ID", "").strip()
NETLIFY_SITE_URL = os.getenv("NETLIFY_SITE_URL", "https://listadeexistenciasdiario.netlify.app/").strip()


def run(command: list[str]) -> str:
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def write_inventory(pdf_path: Path) -> str:
    inventory = extract_inventory(pdf_path)
    INVENTORY_JSON.write_text(
        __import__("json").dumps(inventory, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return (
        f"{inventory['total_productos']} productos, "
        f"{inventory['total_disponibles']} disponibles, "
        f"{inventory['total_agotados']} agotados"
    )


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
    data = json.loads(INVENTORY_JSON.read_text(encoding="utf-8"))
    return {str(item.get("codigo", "")).strip() for item in data.get("productos", []) if item.get("codigo")}


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


def share_message(result: str, summary: str) -> str:
    return (
        f"Listo: {summary}. {result}\n\n"
        f"{links_message()}"
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

    await bot.send_message(chat_id=chat_id, text=f"Recibi PDF: {file_name}. Actualizando inventario...")

    with tempfile.TemporaryDirectory() as temp_dir:
        pdf_path = Path(temp_dir) / file_name
        telegram_file = await bot.get_file(document.file_id)
        await telegram_file.download_to_drive(custom_path=pdf_path)

        try:
            await asyncio.to_thread(sync_from_github)
            summary = await asyncio.to_thread(write_inventory, pdf_path)
            result = await asyncio.to_thread(publish_to_github, summary)
            await bot.send_message(chat_id=chat_id, text=share_message(result, summary))
        except Exception as exc:
            await bot.send_message(chat_id=chat_id, text=error_message(exc))
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
    if not product_code:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "Recibi la imagen, pero falta el codigo.\n\n"
                "Envíala con caption así:\n"
                "codigo 848958043799"
            ),
        )
        return True

    await bot.send_message(chat_id=chat_id, text=f"Recibi imagen para codigo {product_code}. Actualizando...")

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
            telegram_file = await bot.get_file(file_id)
            await telegram_file.download_to_drive(custom_path=temp_path)

            await asyncio.to_thread(sync_from_github)
            exists_in_inventory = product_code in await asyncio.to_thread(product_codes)
            image_path = await asyncio.to_thread(optimize_product_image, temp_path, product_code)
            result = await asyncio.to_thread(update_product_image_mapping, product_code, image_path)

            warning = "" if exists_in_inventory else "\n\nOjo: no encontre ese codigo en el inventario actual, pero deje la imagen guardada para cuando aparezca."
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"Listo. Codigo {product_code}: {result}{warning}\n\n"
                    f"Liga solo celulares:\n{NETLIFY_SITE_URL.rstrip('/')}?celulares=1"
                ),
            )
        except Exception as exc:
            await bot.send_message(
                chat_id=chat_id,
                text=(
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
    if text == "/start":
        await bot.send_message(
            chat_id=chat_id,
            text="Listo. Reenvíame el PDF de inventario y actualizaré la lista de existencias.",
        )
        return
    if text == "/id":
        await bot.send_message(chat_id=chat_id, text=f"Chat ID: {chat_id}")
        return
    if text.lower() in {"/ligas", "ligas", "dame las ligas", "dame las ligas de las listas"}:
        await bot.send_message(chat_id=chat_id, text=links_message())
        return
    if await handle_product_image(bot, update):
        return
    await handle_pdf(bot, update)


async def poll(token: str) -> None:
    bot = Bot(token)
    me = await bot.get_me()
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
