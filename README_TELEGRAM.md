# Actualizacion por Telegram

Este bot recibe PDFs por Telegram, convierte el PDF a `inventario.json`, hace commit a GitHub y Netlify publica el cambio automaticamente.

La forma mas rapida de usarlo es reenviar el PDF al bot por privado. No necesitas agregar el bot al grupo original.

## 1. Crear bot

1. En Telegram abre `@BotFather`.
2. Ejecuta `/newbot`.
3. Guarda el token del bot.
4. Ejecuta `/setprivacy`.
5. Selecciona tu bot.
6. Elige `Disable`, para que el bot pueda leer PDFs enviados al grupo.

## 2. Opcion rapida: reenviar PDF al bot por privado

1. Abre el chat privado con tu bot.
2. Reenvia o adjunta el PDF de inventario.
3. El bot descarga el PDF, actualiza `inventario.json` y hace push a GitHub.
4. Netlify publica el cambio automaticamente.

## 3. Opcion automatica: agregar bot al grupo

1. Agrega el bot al grupo donde llega el PDF.
2. Si el grupo es muy restrictivo, hazlo administrador con permiso para leer mensajes.
3. Envia un PDF de prueba.

## 4. Ejecutar el bot

En una copia local del repositorio:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="TOKEN_DE_BOTFATHER"
python telegram_inventory_bot.py
```

Cuando llegue un PDF, el bot:

1. Lo descarga.
2. Regenera `inventario.json`.
3. Hace commit y push a GitHub.
4. Netlify publica la actualizacion automaticamente.

## Variables opcionales para grupo

Permitir solo un grupo especifico:

```bash
export ALLOWED_CHAT_ID="-1001234567890"
```

Procesar solo PDFs cuyo nombre contenga una palabra:

```bash
export PDF_NAME_CONTAINS="inventario"
```

## Importante

La computadora o servidor donde corra el bot debe permanecer encendido. Para que sea 24/7, conviene correrlo en un servidor como Render, Railway, Fly.io, VPS, o una computadora siempre encendida.
