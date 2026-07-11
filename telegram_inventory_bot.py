#!/usr/bin/env python3
import asyncio
import os
import subprocess
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from parse_inventory_pdf import extract_inventory


ROOT = Path(__file__).resolve().parent
INVENTORY_JSON = ROOT / "inventario.json"
PDF_NAME_CONTAINS = os.getenv("PDF_NAME_CONTAINS", "").lower().strip()
ALLOWED_CHAT_ID = os.getenv("ALLOWED_CHAT_ID", "").strip()


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


def publish_to_github(summary: str) -> str:
    run(["git", "pull", "--rebase", "origin", "main"])
    status = run(["git", "status", "--short", "inventario.json"])
    if not status:
        return "El inventario no tuvo cambios."

    run(["git", "add", "inventario.json"])
    run(["git", "commit", "-m", f"Actualizar inventario diario ({summary})"])
    run(["git", "push", "origin", "main"])
    return "Inventario actualizado en GitHub. Netlify publicara el cambio automaticamente."


async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    await update.message.reply_text(f"Recibi PDF: {file_name}. Actualizando inventario...")

    with tempfile.TemporaryDirectory() as temp_dir:
        pdf_path = Path(temp_dir) / file_name
        telegram_file = await context.bot.get_file(document.file_id)
        await telegram_file.download_to_drive(custom_path=pdf_path)

        try:
            summary = await asyncio.to_thread(write_inventory, pdf_path)
            result = await asyncio.to_thread(publish_to_github, summary)
            await update.message.reply_text(f"Listo: {summary}. {result}")
        except Exception as exc:
            await update.message.reply_text(f"No pude actualizar el inventario: {exc}")
            raise


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Falta TELEGRAM_BOT_TOKEN en variables de entorno.")

    app = Application.builder().token(token).build()
    app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
