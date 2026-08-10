"""Generacion de anuncios de producto con OpenAI y texto exacto con Pillow."""

from __future__ import annotations

import base64
import random
from pathlib import Path

from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont, ImageOps


STYLES = (
    ("tecnologico oscuro", "negro grafito, azul electrico y reflejos metalicos"),
    ("premium minimalista", "marfil, negro elegante y acentos dorados discretos"),
    ("futurista vibrante", "degradados violeta y cyan, luz de estudio y energia visual"),
    ("comercial moderno", "colores intensos, formas geometricas y alto contraste"),
    ("editorial elegante", "fondo limpio, sombras suaves y composicion de revista"),
    ("urbano dinamico", "texturas sutiles, diagonales y luz cinematografica"),
)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold
        else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/SFNS.ttf",
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _clean_lines(features: str) -> list[str]:
    lines = [line.strip(" \t-•") for line in features.splitlines()]
    return [line for line in lines if line][:12]


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    result: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if draw.textbbox((0, 0), candidate, font=font)[2] <= width:
            current = candidate
        else:
            result.append(current)
            current = word
    result.append(current)
    return result


def _overlay_exact_text(image_path: Path, features: str, output_path: Path) -> None:
    lines = _clean_lines(features)
    title = lines[0] if lines else "PRODUCTO DESTACADO"
    details = lines[1:] if len(lines) > 1 else ["Calidad, estilo y tecnologia para tu dia a dia"]

    image = Image.open(image_path).convert("RGB")
    image = ImageOps.fit(image, (1024, 1536), method=Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(image, "RGBA")
    panel_top = 980
    draw.rounded_rectangle((55, panel_top, 969, 1480), radius=38, fill=(8, 12, 20, 226))
    draw.rounded_rectangle((84, panel_top + 40, 104, panel_top + 116), radius=10, fill=(78, 220, 255, 255))

    title_font = _font(48, bold=True)
    body_font = _font(31)
    label_font = _font(23, bold=True)
    draw.text((130, panel_top + 38), "CARACTERISTICAS", font=label_font, fill=(116, 228, 255, 255))

    y = panel_top + 125
    for wrapped in _wrap(draw, title.upper(), title_font, 800)[:2]:
        draw.text((86, y), wrapped, font=title_font, fill="white")
        y += 58
    y += 16

    available = 1435 - y
    body_size = 31
    rendered = []
    line_height = body_size + 12
    while body_size >= 22:
        body_font = _font(body_size)
        rendered = []
        for detail in details:
            rendered.extend(_wrap(draw, detail, body_font, 790))
        line_height = body_size + 12
        if len(rendered) * line_height <= available:
            break
        body_size -= 2

    for line in rendered:
        draw.ellipse((90, y + 11, 103, y + 24), fill=(78, 220, 255, 255))
        draw.text((124, y), line, font=body_font, fill=(245, 247, 250, 255))
        y += line_height

    image.save(output_path, format="JPEG", quality=94, optimize=True)


def generate_advertisement(reference_path: Path, features: str, output_path: Path) -> str:
    """Crea el arte con una referencia y agrega las caracteristicas sin errores."""
    style_name, palette = random.choice(STYLES)
    prompt = f"""
Usa la imagen adjunta como referencia fiel del producto. Crea una publicidad vertical
9:16 de nivel profesional para redes sociales, estilo {style_name}, con {palette}.
Conserva con precision la forma, color, camaras, logotipo y detalles fisicos del producto.
Muestra el producto grande, nitido y atractivo en la zona superior y central, con iluminacion
comercial realista. Reserva aproximadamente el 36 por ciento inferior para una tarjeta oscura,
limpia y uniforme donde despues se colocara texto. No escribas letras, palabras, numeros,
marcas inventadas, especificaciones ni texto de relleno dentro de la imagen. Sin personas.
""".strip()

    client = OpenAI()
    with reference_path.open("rb") as reference:
        result = client.images.edit(
            model="gpt-image-2",
            image=reference,
            prompt=prompt,
            size="1024x1536",
            quality="medium",
        )
    generated_path = output_path.with_suffix(".generated.png")
    generated_path.write_bytes(base64.b64decode(result.data[0].b64_json))
    try:
        _overlay_exact_text(generated_path, features, output_path)
    finally:
        generated_path.unlink(missing_ok=True)
    return style_name
