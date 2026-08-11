"""Generacion local de anuncios y opcion futura de generacion con OpenAI."""

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

LOCAL_THEMES = (
    ("tecnologico oscuro", (7, 12, 24), (16, 54, 84), (64, 224, 255)),
    ("premium dorado", (20, 17, 14), (72, 53, 28), (235, 190, 92)),
    ("neon violeta", (20, 8, 42), (76, 20, 110), (211, 82, 255)),
    ("azul comercial", (6, 30, 68), (16, 102, 158), (84, 220, 255)),
    ("rojo dinamico", (39, 7, 14), (126, 18, 35), (255, 88, 104)),
    ("verde futurista", (5, 28, 24), (11, 91, 71), (57, 235, 170)),
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


def _gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    width, height = size
    strip = Image.new("RGB", (1, height))
    pixels = strip.load()
    for y in range(height):
        ratio = y / max(1, height - 1)
        pixels[0, y] = tuple(int(a + (b - a) * ratio) for a, b in zip(top, bottom))
    return strip.resize((width, height))


def _rounded_product(reference_path: Path, size: tuple[int, int]) -> Image.Image:
    product = Image.open(reference_path).convert("RGB")
    product.thumbnail(size, Image.Resampling.LANCZOS)
    frame = Image.new("RGBA", size, (255, 255, 255, 0))
    x = (size[0] - product.width) // 2
    y = (size[1] - product.height) // 2
    frame.paste(product, (x, y))
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=44, fill=255)
    frame.putalpha(mask)
    return frame


def generate_local_advertisement(reference_path: Path, features: str, output_path: Path) -> str:
    """Compone el anuncio predeterminado de Celu Center sin red ni creditos."""
    style_name = "Celu Center claro"
    accent_blue = (12, 137, 202)
    accent_green = (84, 171, 35)
    accent_purple = (103, 45, 154)
    navy = (3, 35, 68)
    canvas = _gradient((1024, 1536), (255, 255, 255), (239, 245, 252)).convert("RGBA")
    draw = ImageDraw.Draw(canvas, "RGBA")

    # Aros luminosos inspirados en la referencia de marca.
    draw.ellipse((360, -160, 1130, 610), outline=(214, 226, 246, 180), width=18)
    draw.ellipse((425, -90, 1070, 555), outline=(228, 237, 250, 220), width=7)
    draw.ellipse((505, -10, 995, 480), fill=(213, 230, 255, 80))

    # Logotipo tipografico de la marca.
    logo_font = _font(60, bold=True)
    draw.text((38, 30), "celu", font=logo_font, fill=(*accent_blue, 255))
    draw.text((38, 88), "center", font=logo_font, fill=(*accent_green, 255))
    draw.arc((248, 38, 310, 100), 265, 85, fill=(244, 156, 0, 255), width=10)
    draw.arc((258, 48, 300, 90), 265, 85, fill=(244, 156, 0, 255), width=8)

    lines = _clean_lines(features)
    title = lines[0] if lines else "PRODUCTO DESTACADO"
    details = lines[1:] if len(lines) > 1 else ["Calidad y tecnologia para tu dia a dia"]

    # Nombre del producto a la izquierda; fotografia original a la derecha.
    title_font = _font(48, bold=True)
    y = 205
    for title_line in _wrap(draw, title.upper(), title_font, 420)[:4]:
        draw.text((38, y), title_line, font=title_font, fill=(*navy, 255))
        y += 58
    draw.text((40, y + 16), "Tecnologia que te acompana.", font=_font(27), fill=(28, 43, 59, 255))
    draw.text((40, y + 50), "Simple, potente y confiable.", font=_font(27, bold=True), fill=(*accent_green, 255))

    draw.rounded_rectangle((470, 102, 994, 748), radius=46, fill=(0, 27, 60, 35))
    draw.rounded_rectangle((456, 88, 980, 734), radius=46, fill=(255, 255, 255, 238), outline=(210, 223, 240, 255), width=3)
    product = _rounded_product(reference_path, (492, 614))
    canvas.alpha_composite(product, (472, 104))
    draw.ellipse((510, 682, 945, 760), fill=(170, 187, 212, 60))

    # Separador y tabla clara de caracteristicas.
    draw.polygon(((0, 700), (360, 700), (405, 770), (0, 770)), fill=(*navy, 255))
    draw.text((38, 715), "CARACTERISTICAS", font=_font(27, bold=True), fill="white")
    draw.text((512, 796), "TODO LO QUE NECESITAS", anchor="mm", font=_font(32, bold=True), fill=(*navy, 255))
    draw.line((70, 830, 954, 830), fill=(202, 214, 230, 255), width=2)

    card_colors = (accent_green, accent_blue, accent_purple, accent_blue)
    count = min(len(details), 10)
    rows = (count + 1) // 2
    card_height = min(112, max(82, 480 // max(1, rows)))
    body_size = 25 if rows <= 4 else 21
    for index, detail in enumerate(details[:10]):
        col = index % 2
        row = index // 2
        x = 55 + col * 485
        card_y = 852 + row * card_height
        color = card_colors[index % len(card_colors)]
        draw.rounded_rectangle((x, card_y, x + 440, card_y + card_height - 10), radius=18, fill=(255, 255, 255, 205), outline=(215, 224, 235, 255), width=2)
        draw.ellipse((x + 18, card_y + 18, x + 64, card_y + 64), outline=(*color, 255), width=4)
        draw.ellipse((x + 32, card_y + 32, x + 50, card_y + 50), fill=(*color, 255))
        detail_font = _font(body_size, bold=True)
        wrapped = _wrap(draw, detail, detail_font, 345)
        text_y = card_y + 18 if len(wrapped) <= 2 else card_y + 8
        for detail_line in wrapped[:3]:
            draw.text((x + 80, text_y), detail_line, font=detail_font, fill=(15, 25, 38, 255))
            text_y += body_size + 7

    # Banda final de marca.
    draw.rounded_rectangle((24, 1370, 1000, 1460), radius=25, fill=(*navy, 255))
    footer = ("RENDIMIENTO CONFIABLE", "DISENO MODERNO", "FACIL DE USAR", "CALIDAD A TU ALCANCE")
    for index, text in enumerate(footer):
        x = 55 + index * 240
        if index:
            draw.line((x - 18, 1390, x - 18, 1440), fill=(255, 255, 255, 85), width=2)
        draw.text((x, 1404), text, font=_font(16, bold=True), fill="white")
    draw.text((512, 1492), "S E N C I L L O  ·  P O T E N T E  ·  C O N F I A B L E", anchor="mm", font=_font(17, bold=True), fill=(*navy, 255))

    canvas.convert("RGB").save(output_path, format="JPEG", quality=94, optimize=True)
    return style_name


def generate_advertisement(
    reference_path: Path,
    features: str,
    output_path: Path,
    mode: str = "local",
) -> str:
    """Crea el anuncio localmente; ``mode=openai`` conserva la opcion de API."""
    if mode.lower() != "openai":
        return generate_local_advertisement(reference_path, features, output_path)

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
