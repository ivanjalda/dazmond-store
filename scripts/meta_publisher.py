#!/usr/bin/env python3
"""
DAZMOND — Publicación automática en Instagram (@dazmondco) y en la página de Facebook.

Se ejecuta cada hora junto a la tienda, pero solo publica en las horas elegidas
(por defecto 2 veces al día, a mediodía y por la noche de Estados Unidos).
En cada turno:
  - Instagram: un carrusel con 3 productos distintos (o una foto si solo hay una).
  - Facebook: una publicación con la foto y el enlace directo al producto en dazmond.com.
Rota los productos para no repetir y prioriza novedades y diseños propios.

Necesita el secreto META_PAGE_TOKEN (token permanente de la página). Sin él no hace nada.
Guarda lo publicado en data/meta_publisher.json.
"""

import json
import os
import random
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dz_common import load_json, notify, save_json  # noqa: E402

GRAPH = "https://graph.facebook.com/v23.0"
DEFAULTS = {
    "enabled": True,
    "page_id": "1379434155262838",
    "ig_user_id": "17841441338721718",
    "slots_utc": [17, 1],          # 12:00 y 20:00 en Nueva York (invierno)
    "products_per_post": 3,
}
TAGS = "#DAZMOND #LuxuryFashion #MadeToOrder #DesignerClothing #LuxuryStyle #AmericanFashion #OOTD #StyleInspo"


def log(m):
    print(f"[meta] {m}", flush=True)


def api(path, params, method="POST"):
    data = urllib.parse.urlencode(params).encode()
    url = f"{GRAPH}/{path}"
    if method == "GET":
        req = urllib.request.Request(url + "?" + data.decode())
    else:
        req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(e.read().decode()[:400])


def image_url(p, site):
    for src in (p.get("display_images") or []) + (p.get("images_original") or []) + (p.get("raw_images") or []):
        if not src:
            continue
        if src.startswith("http"):
            return src
        return f"{site}/{src.lstrip('/')}"
    return None


def pick(products, state, n, rng):
    used = set(state.get("used", [])[-600:])
    ok = [p for p in products if p.get("available", True) and not p.get("hidden") and not p.get("draft")
          and p.get("price_usd", 0) > 0]
    fresh = [p for p in ok if p["id"] not in used]
    if len(fresh) < n:
        state["used"] = []
        fresh = ok
    # primero diseños propios y novedades, luego el resto
    own = [p for p in fresh if "dazmond" in (p.get("source_text", "") + p.get("supplier", "")).lower()
           or p.get("badge") in ("new", "top1", "bestseller")]
    pool = own + [p for p in fresh if p not in own]
    head = pool[:40]
    rng.shuffle(head)
    out, fams = [], set()
    for p in head + pool[40:]:
        if p.get("fam") in fams:
            continue
        fams.add(p.get("fam"))
        out.append(p)
        if len(out) >= n:
            break
    return out


def caption(items, site):
    lines = ["New from the House of DAZMOND ✦", ""]
    for p in items:
        lines.append(f"• {p.get('name_en', 'DAZMOND piece')} — ${round(p.get('price_usd', 0))}")
    lines += ["", "Made to order. Free shipping worldwide.", f"Shop now: link in bio · {site.replace('https://', '')}",
              "", TAGS]
    return "\n".join(lines)


def publish_instagram(ig, token, items, site):
    urls = [u for u in (image_url(p, site) for p in items) if u]
    if not urls:
        raise RuntimeError("ningún producto tiene foto pública")
    cap = caption(items, site)
    if len(urls) == 1:
        c = api(f"{ig}/media", {"image_url": urls[0], "caption": cap, "access_token": token})
        cid = c["id"]
    else:
        kids = []
        for u in urls[:10]:
            k = api(f"{ig}/media", {"image_url": u, "is_carousel_item": "true", "access_token": token})
            kids.append(k["id"])
        c = api(f"{ig}/media", {"media_type": "CAROUSEL", "children": ",".join(kids), "caption": cap,
                                "access_token": token})
        cid = c["id"]
    for _ in range(20):  # esperar a que Instagram procese las fotos
        st = api(cid, {"fields": "status_code", "access_token": token}, "GET").get("status_code")
        if st == "FINISHED":
            break
        if st == "ERROR":
            raise RuntimeError("Instagram no pudo procesar las fotos")
        time.sleep(5)
    r = api(f"{ig}/media_publish", {"creation_id": cid, "access_token": token})
    return r.get("id")


def publish_facebook(page, token, p, site):
    url = image_url(p, site)
    link = f"{site}/p/{p['id']}/?lang=en&cur=USD"
    msg = (f"{p.get('name_en', 'DAZMOND')} — ${round(p.get('price_usd', 0))}\n"
           f"Made to order · Free shipping worldwide\n\nShop: {link}\n\n{TAGS}")
    if url:
        r = api(f"{page}/photos", {"url": url, "message": msg, "access_token": token})
    else:
        r = api(f"{page}/feed", {"message": msg, "link": link, "access_token": token})
    return r.get("post_id") or r.get("id")


def main():
    token = os.environ.get("META_PAGE_TOKEN", "").strip()
    if not token:
        log("Falta el secreto META_PAGE_TOKEN: hoy no publico.")
        return 0
    cfg = load_json("config.json", {})
    conf = {**DEFAULTS, **cfg.get("meta_publisher", {})}
    if not conf.get("enabled", True):
        return 0
    site = (cfg.get("site_url") or "https://dazmond.com").rstrip("/")
    now = datetime.now(timezone.utc)
    slot = f"{now:%Y-%m-%d}-{now.hour}"
    state = load_json("meta_publisher.json", {"done": [], "used": [], "log": []})
    force = os.environ.get("META_FORCE") == "1"
    if not force and (now.hour not in conf["slots_utc"] or slot in state["done"]):
        log(f"No toca publicar ahora (hora UTC {now.hour}).")
        return 0
    products = load_json("products.json", {"products": []}).get("products", [])
    rng = random.Random(slot)
    items = pick(products, state, conf["products_per_post"], rng)
    if not items:
        log("No hay productos con foto para publicar.")
        return 0
    result = {"slot": slot, "ids": [p["id"] for p in items]}
    try:
        result["instagram"] = publish_instagram(conf["ig_user_id"], token, items, site)
        log(f"Instagram publicado: {result['instagram']}")
    except Exception as e:
        result["instagram_error"] = str(e)[:300]
        log(f"Instagram falló: {e}")
    try:
        result["facebook"] = publish_facebook(conf["page_id"], token, items[0], site)
        log(f"Facebook publicado: {result['facebook']}")
    except Exception as e:
        result["facebook_error"] = str(e)[:300]
        log(f"Facebook falló: {e}")
    state["done"] = (state["done"] + [slot])[-200:]
    state["used"] = (state.get("used", []) + result["ids"])[-600:]
    state["log"] = (state.get("log", []) + [result])[-100:]
    save_json("meta_publisher.json", state)
    if "instagram_error" in result or "facebook_error" in result:
        notify("⚠️ Redes: " + "; ".join(f"{k}: {v[:150]}" for k, v in result.items() if k.endswith("_error")))
    else:
        notify(f"✦ Publicado en Instagram y Facebook: {', '.join(p.get('name_es', p['id']) for p in items)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
