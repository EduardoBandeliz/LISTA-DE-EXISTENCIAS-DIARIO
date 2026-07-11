#!/usr/bin/env python3
import asyncio
import os
import subprocess
import tempfile
from pathlib import Path

from telegram import Bot, Update
from telegram.error import NetworkError, TimedOut

from parse_inventory_pdf import extract_inventory


ROOT = Path(__file__).resolve().parent
INVENTORY_JSON = ROOT / "inventario.json"
PDF_NAME_CONTAINS = os.getenv("PDF_NAME_CONTAINS", "").lower().strip()
ALLOWED_CHAT_ID = os.getenv("ALLOWED_CHAT_ID", "").strip()
NETLIFY_SITE_URL = os.getenv("NETLIFY_SITE_URL", "https://listadeexistenciasdiario.netlify.app/").strip()
DISCOUNT_AMOUNT = int(os.getenv("DISCOUNT_AMOUNT", "30"))


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


def share_message(result: str, summary: str) -> str:
    normal_url = NETLIFY_SITE_URL
    discount_url = f"{NETLIFY_SITE_URL.rstrip('/')}?descuento={DISCOUNT_AMOUNT}"
    return (
        f"Listo: {summary}. {result}\n\n"
        f"Liga precio normal:\n{normal_url}\n\n"
        f"Liga con descuento de ${DISCOUNT_AMOUNT} pesos:\n{discount_url}"
    )


def error_message(exc: Exception) -> str:
    normal_url = NETLIFY_SITE_URL
    discount_url = f"{NETLIFY_SITE_URL.rstrip('/')}?descuento={DISCOUNT_AMOUNT}"
    return (
        f"No pude actualizar el inventario: {exc}\n\n"
        f"Liga actual precio normal:\n{normal_url}\n\n"
        f"Liga actual con descuento de ${DISCOUNT_AMOUNT} pesos:\n{discount_url}"
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
