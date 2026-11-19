"""
DAZMOND & Co. — Lector de fotos con inteligencia artificial (Gemini, capa gratuita).

Mira las fotos de un producto y devuelve:
  - qué es cada foto: producto, modelo, guía de tallas u otra cosa,
  - la tabla de tallas LEÍDA de la foto de la guía (en cm y pulgadas si aparecen),
  - nombre, tipo, para quién, color, material y tallas disponibles.

Necesita el secreto GEMINI_API_KEY (gratis en Google AI Studio). Sin él no hace nada.
"""

import base64
import json
import os
import shutil
import subprocess
import tempfile

from dz_common import env, load_json, request

PROMPT = """You are the product editor of DAZMOND & Co., an American luxury fashion house.
You receive the photos of ONE product from a print-on-demand supplier, in order (photo 1, photo 2...).
Extra info from the owner (may be empty): {context}

Return ONLY a JSON object with these keys:
- "photos": a list, one entry per photo in the same order, each {{"n": number, "kind": one of
  "product" (clean product shot without person), "model" (worn by a person or lifestyle scene),
  "size_guide" (a size chart / measurement table / how-to-measure image), "detail" (close-up of fabric or print),
  "other" (logos, banners, text, unrelated), "sharp": true/false (is the garment and its print clearly visible?)}}
- "size_chart": null, or if any photo is a size guide, READ IT CAREFULLY and copy it exactly:
  {{"unit": "cm" or "inch" or "cm/inch", "headers": [...column names in English...],
    "headers_es": [...same columns in Spanish...], "rows": [[...one row per size, exact numbers as text...]],
    "notes_en": "short how-to-measure note if present", "notes_es": "same in Spanish"}}
- "sizes": list of sizes available (from the guide or the photos), e.g. ["S","M","L"] or ["36","37"]
- "type": short English product type, e.g. "t-shirt", "dress", "jumpsuit", "bodysuit", "boots", "sneakers", "pillow"
- "gender": "men", "women" or "unisex"
- "color": main color in English, or ""
- "material": main material in English if it is written somewhere, or ""
- "title_en": a short elegant product name in English (max 6 words), no supplier names
Never invent measurements: if a number is unreadable, write "?"."""


def ready():
    return bool(env("GEMINI_API_KEY"))


def _shrink(path):
    """Reduce la foto a 1280 px para mandarla rápido (si hay FFmpeg)."""
    if not shutil.which("ffmpeg"):
        return path
    out = tempfile.mktemp(suffix=".jpg")
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-vf",
                        "scale='min(1280,iw)':-2", "-q:v", "4", out], capture_output=True)
    return out if r.returncode == 0 and os.path.exists(out) else path


def analyze(paths, context=""):
    """paths: fotos locales en orden. Devuelve el diccionario descrito arriba, o None."""
    if not ready() or not paths:
        return None
    model = load_json("config.json", {}).get("ai", {}).get("model", "gemini-2.5-flash")
    parts = [{"text": PROMPT.format(context=context or "-")}]
    for i, p in enumerate(paths[:12]):
        small = _shrink(p)
        with open(small, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        parts.append({"text": f"Photo {i + 1}:"})
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": data}})
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"}}
    last = None
    for m in [model, "gemini-flash-latest", "gemini-2.0-flash"]:  # si un modelo se retira, prueba el siguiente
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={env('GEMINI_API_KEY')}"
        try:
            res = request(url, "POST", body=body, timeout=180, retries=2)
            text = res["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
        except Exception as e:
            last = e
            if "404" not in str(e):
                break
    print(f"[ia] No se pudieron leer las fotos: {str(last)[:160]}")
    return None


def compress(src, dest_noext, width=1400):
    """Guarda la foto en la web, comprimida (WebP) para que cargue rápido. Devuelve la ruta final."""
    if shutil.which("ffmpeg"):
        dest = dest_noext + ".webp"
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vf",
                            f"scale='min({width},iw)':-2", "-quality", "82", dest], capture_output=True)
        if r.returncode == 0 and os.path.exists(dest):
            return dest
    ext = os.path.splitext(src)[1].lower() or ".jpg"
    dest = dest_noext + ext
    shutil.copy(src, dest)
    return dest


def build_from_photos(local_paths, pid, context=""):
    """Guarda las fotos en assets/products/, las clasifica y lee la guía de tallas.
    Devuelve los campos para el catálogo (images, size_guide, size_chart, sizes, type, gender...)."""
    from dz_common import ROOT
    out_dir = os.path.join(ROOT, "assets", "products")
    os.makedirs(out_dir, exist_ok=True)
    info = analyze(local_paths, context) or {}
    kinds = {int(ph.get("n", 0)): ph for ph in info.get("photos", []) if isinstance(ph, dict)}
    shop, guides = [], []
    for i, src in enumerate(local_paths):
        ph = kinds.get(i + 1, {})
        kind = ph.get("kind", "product")
        if kind == "other":
            continue
        saved = compress(src, os.path.join(out_dir, f"{pid}-{i + 1}"), 1600 if kind == "size_guide" else 1400)
        rel = os.path.relpath(saved, ROOT).replace("\\", "/")
        if kind == "size_guide":
            guides.append(rel)
        elif ph.get("sharp", True) is not False:
            shop.append(rel)
    return {
        "images": "|".join(shop),
        "size_guide": "|".join(guides),
        "size_chart": json.dumps(info["size_chart"], ensure_ascii=False) if info.get("size_chart") else "",
        "ai_sizes": "|".join(str(s) for s in info.get("sizes") or []),
        "ai_type": info.get("type", ""), "ai_gender": info.get("gender", ""),
        "ai_color": info.get("color", ""), "ai_material": info.get("material", ""),
        "ai_title": info.get("title_en", ""),
    }


COPY_PROMPT = """You are the head copywriter of DAZMOND & Co., an American luxury fashion house (registered US brand,
made-to-order pieces, worldwide shipping). Rewrite this product COMPLETELY in the house style, in Spanish and English.

Product data from the supplier (may be messy, in any language, with supplier names or marketing junk):
{data}

Rules:
- Elegant, sensory, confident, like Saint Laurent or Hugo Boss. Short sentences. No emojis, no exclamation marks.
- NEVER invent facts: no fabrics, origins ("Italian", "handmade"), certifications or awards that are not in the data.
- Never mention the supplier, print-on-demand, dropshipping, AliExpress or any other brand.
- You may say it is made to order (confeccionada bajo demanda) and shipped worldwide.
- "name": max 5 words, refined, no supplier codes (you may add a collection word like Noir, Riviera, Atelier, Monogram).
- "desc": 60-110 words, about the piece, the silhouette, how and when to wear it.
- "details": short lines (one per line, each starting with "· ") with ONLY real facts from the data:
  composition/material, fit, closure, care instructions, notes. If there are none, write just the made-to-order line.

Return ONLY JSON: {{"name_es": "", "name_en": "", "desc_es": "", "desc_en": "", "details_es": "", "details_en": ""}}"""


def write_copy(data_text, image_path=None):
    """Escribe nombre, descripción y detalles de lujo (ES/EN) para un producto. Devuelve dict o None."""
    if not ready():
        return None
    model = load_json("config.json", {}).get("ai", {}).get("model", "gemini-2.5-flash")
    parts = [{"text": COPY_PROMPT.format(data=data_text[:3000])}]
    if image_path and os.path.exists(image_path):
        small = _shrink(image_path)
        with open(small, "rb") as f:
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(f.read()).decode()}})
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.6, "responseMimeType": "application/json"}}
    for m in [model, "gemini-flash-latest", "gemini-2.0-flash"]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={env('GEMINI_API_KEY')}"
        try:
            res = request(url, "POST", body=body, timeout=120, retries=2)
            out = json.loads(res["candidates"][0]["content"]["parts"][0]["text"])
            if all(out.get(k) for k in ("name_es", "name_en", "desc_es", "desc_en")):
                return out
            return None
        except Exception as e:
            if "404" not in str(e):
                print(f"[ia] No se pudo escribir el texto: {str(e)[:120]}")
                return None
    return None


LAST_ERROR = ""

ENRICH_PROMPT = """You are the product editor and head copywriter of DAZMOND & Co., an American luxury fashion house
(registered US brand, made-to-order pieces, worldwide shipping). You receive the photos of ONE product, in order,
and the messy supplier data. Do everything below in ONE answer.

Supplier data: {data}

A) Photos: for each photo say what it is: "product" (clean shot, no person), "model" (worn by a person / lifestyle),
   "size_guide" (size chart, measurement table or how-to-measure), "detail" (close-up), "other" (logo, banner, text,
   unrelated). Also "sharp": true/false (garment and print clearly visible).
B) If a photo is a size guide, READ IT CAREFULLY and copy the table exactly (never invent numbers, use "?" if unreadable).
C) Rewrite the product in the house style, in Spanish and English: elegant, sensory, confident (Saint Laurent, Hugo Boss).
   Short sentences, no emojis, no exclamation marks. NEVER invent facts (no fabrics, origins, "handmade", awards not in
   the data). Never mention the supplier, print-on-demand or other brands. You may say made to order and worldwide shipping.
   name: max 5 words, refined. desc: 60-110 words. details: lines starting with "· " with ONLY real facts
   (composition, fit, closure, care); if none, just the made-to-order line.

Return ONLY JSON:
{{"photos": [{{"n": 1, "kind": "...", "sharp": true}}],
  "size_chart": null or {{"unit": "", "headers": [], "headers_es": [], "rows": [[]], "notes_en": "", "notes_es": ""}},
  "sizes": [], "type": "", "gender": "men|women|unisex", "color": "", "material": "",
  "name_es": "", "name_en": "", "desc_es": "", "desc_en": "", "details_es": "", "details_en": ""}}"""


def enrich(paths, data_text):
    """Una sola llamada por producto: clasifica fotos, lee la guía de tallas y escribe los textos. dict o None."""
    global LAST_ERROR
    LAST_ERROR = ""
    if not ready():
        return None
    model = load_json("config.json", {}).get("ai", {}).get("model", "gemini-2.5-flash")
    parts = [{"text": ENRICH_PROMPT.format(data=(data_text or "-")[:3000])}]
    for i, p in enumerate(paths[:10]):
        small = _shrink(p)
        with open(small, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        parts.append({"text": f"Photo {i + 1}:"})
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": data}})
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json"}}
    for m in [model, "gemini-flash-latest", "gemini-2.0-flash"]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={env('GEMINI_API_KEY')}"
        try:
            res = request(url, "POST", body=body, timeout=150, retries=1)
            out = json.loads(res["candidates"][0]["content"]["parts"][0]["text"])
            return out if isinstance(out, dict) else None
        except Exception as e:
            LAST_ERROR = str(e)[:200]
            if "404" not in LAST_ERROR:
                return None
    return None


AUDIT_PROMPT = """You are the quality inspector of a luxury fashion store. Look at the photo.
The store currently lists it as: {claim}
Answer ONLY JSON: {{"ok": true/false (is that listing correct for what you see?),
 "type": "short English product type you actually see (e.g. t-shirt, baseball jersey, jacket, dress, jumpsuit, sneakers)",
 "gender": "men|women|unisex (who it is made for, judging by cut and model)",
 "photo_ok": true/false (garment clearly visible, not a logo, banner or size chart)}}"""


def audit(image_path, claim):
    """Revisión de control: ¿la foto corresponde a la sección y al público donde está? dict o None."""
    global LAST_ERROR
    if not ready() or not image_path or not os.path.exists(image_path):
        return None
    model = load_json("config.json", {}).get("ai", {}).get("model", "gemini-2.5-flash")
    with open(_shrink(image_path), "rb") as f:
        data = base64.b64encode(f.read()).decode()
    body = {"contents": [{"role": "user", "parts": [{"text": AUDIT_PROMPT.format(claim=claim)},
                                                    {"inline_data": {"mime_type": "image/jpeg", "data": data}}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={env('GEMINI_API_KEY')}"
    try:
        res = request(url, "POST", body=body, timeout=90, retries=1)
        return json.loads(res["candidates"][0]["content"]["parts"][0]["text"])
    except Exception as e:
        LAST_ERROR = str(e)[:200]
        return None
