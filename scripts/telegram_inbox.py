#!/usr/bin/env python3
"""
DAZMOND & Co. — Buzón del bot de Telegram (solo Python estándar).

Desde el móvil, le mandas al bot:

  1) PRODUCTOS de POPCUSTOMS, Inkpod o InterestPrint (o de cualquier proveedor):
     las fotos del producto (de 1 a 10, mejor como álbum) y en el texto de UNA de ellas:

        PRODUCTO
        Proveedor: POPCUSTOMS
        Nombre: Botas Chelsea de piel
        Coste: 46
        Envío: 10
        Tallas: 40, 41, 42, 43, 44
        Tipo: botas
        Para: hombre
        Enlace: (opcional) la dirección del producto en el proveedor

  2) DISEÑOS para crear productos solos en Printify:
     la imagen como ARCHIVO (no como foto, para no perder calidad) con el texto:
        DISEÑO
        Nombre: Noir Monogram
        Productos: camiseta, sudadera-capucha, cojin      (opcional)

  3) /ayuda  → el bot te recuerda estos modelos.

Solo obedece a TU chat (TELEGRAM_CHAT_ID). Cualquier otra persona es ignorada.
"""

import csv
import io
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dz_common import DATA, ROOT, TODAY, env, load_json, notify, save_json, slug, telegram, tg_ready  # noqa: E402

CSV_PATH = os.path.join(DATA, "suppliers", "telegram.csv")
FIELDS = ["supplier", "supplier_id", "title", "type", "gender", "color", "material", "cost_usd", "shipping_usd",
          "sizes", "images", "model_images", "stripe_payment_link", "stripe_link_id", "stock", "description", "source_url"]

HELP = """✦ DAZMOND & Co. · Cómo mandarme cosas

1️⃣ PRODUCTO de POPCUSTOMS, Inkpod o InterestPrint
Manda las fotos del producto (álbum de hasta 10) y escribe en el texto:

PRODUCTO
Proveedor: POPCUSTOMS
Nombre: Botas Chelsea de piel
Coste: 46
Envío: 10
Tallas: 40, 41, 42, 43, 44
Tipo: botas
Para: hombre

(Coste y envío en dólares. «Para» puede ser hombre, mujer o unisex.)

2️⃣ DISEÑO para crear productos solos en Printify
Manda la imagen como ARCHIVO (📎 → Archivo) con el texto:

DISEÑO
Nombre: Noir Monogram
Productos: camiseta, sudadera-capucha, cojin

(«Productos» es opcional.)"""

KEYS = {
    "proveedor": "supplier", "nombre": "title", "coste": "cost_usd", "costo": "cost_usd", "envio": "shipping_usd",
    "tallas": "sizes", "talla": "sizes", "tipo": "type", "para": "gender", "color": "color", "material": "material",
    "stock": "stock", "unidades": "stock", "descripcion": "description", "enlace": "source_url", "productos": "productos",
}
GENDER = {"hombre": "men", "mujer": "women", "unisex": "unisex", "men": "men", "women": "women"}


def log(msg):
    print(f"[bot] {msg}", flush=True)


def reply(text):
    try:
        telegram("sendMessage", {"chat_id": env("TELEGRAM_CHAT_ID"), "text": text})
    except Exception:
        pass


def parse_caption(text):
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    if not lines:
        return None, {}
    kind = slug(lines[0]).upper()
    data = {}
    for l in lines[1:]:
        if ":" in l:
            k, v = l.split(":", 1)
            k = KEYS.get(slug(k).replace("-", ""), None)
            if k:
                data[k] = v.strip()
    return kind, data


def download(file_id, dest_noext):
    info = telegram("getFile", {"file_id": file_id})["result"]
    ext = os.path.splitext(info["file_path"])[1] or ".jpg"
    url = f"https://api.telegram.org/file/bot{env('TELEGRAM_BOT_TOKEN')}/{info['file_path']}"
    dest = dest_noext + ext.lower()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "DAZMOND"}), timeout=120) as r, \
            open(dest, "wb") as f:
        f.write(r.read())
    return dest


def file_of(msg):
    """Devuelve (file_id, es_archivo) de la foto o imagen-archivo del mensaje."""
    if msg.get("photo"):
        return msg["photo"][-1]["file_id"], False
    doc = msg.get("document")
    if doc and (doc.get("mime_type", "").startswith("image/") or
                doc.get("file_name", "").lower().endswith((".png", ".jpg", ".jpeg", ".webp"))):
        return doc["file_id"], True
    return None, False


def load_rows():
    if not os.path.exists(CSV_PATH):
        return []
    with open(CSV_PATH, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def save_rows(rows):
    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())


def main():
    if not tg_ready():
        log("Sin secretos de Telegram: el buzón está apagado.")
        return 0
    me = str(env("TELEGRAM_CHAT_ID"))
    state = load_json("telegram_state.json", {"offset": 0})
    updates = telegram("getUpdates", {"offset": state.get("offset", 0), "timeout": 0,
                                      "allowed_updates": '["message"]'}).get("result", [])
    if not updates:
        log("No hay mensajes nuevos.")
        return 0
    groups, singles = {}, []
    for u in updates:
        state["offset"] = u["update_id"] + 1
        m = u.get("message") or {}
        if str((m.get("chat") or {}).get("id")) != me:
            continue  # solo te obedece a ti
        if m.get("media_group_id"):
            groups.setdefault(m["media_group_id"], []).append(m)
        else:
            singles.append([m])
    rows = load_rows()
    registry = load_json("disenos.json", {"files": {}})
    for msgs in list(groups.values()) + singles:
        caption = next((m.get("caption") or m.get("text") for m in msgs if (m.get("caption") or m.get("text"))), "")
        if caption.strip().lower() in ("/ayuda", "/start", "ayuda", "/help"):
            reply(HELP)
            continue
        kind, data = parse_caption(caption)
        files = [x for x in (file_of(m) for m in msgs) if x[0]]
        try:
            if kind == "DISENO" and files:
                name = data.get("title") or "Dazmond"
                tpl = "-".join(slug(p) for p in re.split(r"[,;]+", data.get("productos", "")) if p.strip())
                base = os.path.join(ROOT, "disenos", slug(name) + (f"__{tpl}" if tpl else ""))
                path = download(files[0][0], base)
                registry["files"].pop(os.path.basename(path), None)
                warn = "" if files[0][1] else "\n(Consejo: mándalo como ARCHIVO para no perder calidad.)"
                reply(f"✓ Diseño «{name}» recibido. En menos de una hora estarán creados sus productos.{warn}")
                log(f"Diseño recibido: {os.path.basename(path)}")
            elif kind == "PRODUCTO" and files:
                title = data.get("title")
                supplier = data.get("supplier") or "Proveedor"
                if not title or not data.get("cost_usd"):
                    reply("⚠️ Me falta el Nombre o el Coste. Escribe /ayuda para ver el modelo.")
                    continue
                pid = f"{slug(supplier)}-{slug(title)}"[:60]
                imgs = []
                for i, (fid, _) in enumerate(files[:10]):
                    dest = download(fid, os.path.join(ROOT, "assets", "products", f"{pid}-{i + 1}"))
                    imgs.append(os.path.relpath(dest, ROOT).replace("\\", "/"))
                cost = re.sub(r"[^0-9.,]", "", data.get("cost_usd", "0")).replace(",", ".")
                ship = re.sub(r"[^0-9.,]", "", data.get("shipping_usd", "0")).replace(",", ".") or "0"
                row = {"supplier": supplier, "supplier_id": slug(title)[:40], "title": title,
                       "type": data.get("type", ""), "gender": GENDER.get(slug(data.get("gender", "")), ""),
                       "color": data.get("color", ""), "material": data.get("material", ""),
                       "cost_usd": cost, "shipping_usd": ship,
                       "sizes": "|".join(s.strip() for s in re.split(r"[,;/|]+", data.get("sizes", "")) if s.strip()),
                       "images": "|".join(imgs), "stock": data.get("stock", ""),
                       "description": data.get("description", ""), "source_url": data.get("source_url", "")}
                rows = [r for r in rows if not (r.get("supplier") == supplier and r.get("supplier_id") == row["supplier_id"])]
                rows.append(row)
                reply(f"✓ «{title}» de {supplier} recibido con {len(imgs)} foto(s). "
                      f"Aparecerá en la web en menos de una hora, con su precio y su sección.")
                log(f"Producto recibido de {supplier} con {len(imgs)} fotos")
            elif files:
                reply("No sé qué hacer con estas fotos. La primera línea del texto debe ser PRODUCTO o DISEÑO. Escribe /ayuda.")
            elif caption:
                reply("Hola. Escribe /ayuda para ver cómo mandarme productos y diseños.")
        except Exception as e:
            reply(f"⚠️ No pude guardar lo que me mandaste: {str(e)[:200]}")
            log(f"Error: {str(e)[:160]}")
    save_rows(rows)
    save_json("disenos.json", registry)
    save_json("telegram_state.json", state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
