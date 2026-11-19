#!/usr/bin/env python3
"""
DAZMOND & Co. — Motor autónomo de la tienda (solo Python estándar, sin instalar nada).

Qué hace cada vez que se ejecuta (GitHub Actions lo lanza cada 24 h):
  1. Lee los productos nuevos de los 5 proveedores:
       - Archivos CSV en data/suppliers/  (Printful, POPCUSTOMS, Inkpod, InterestPrint, Printify...)
       - API de Printify, si existen los secretos PRINTIFY_TOKEN y PRINTIFY_SHOP_ID
  2. Clasifica cada producto en su menú y subcategoría exactos.
  3. Calcula el precio de venta y el margen.
  4. Escribe el nombre y la descripción de lujo en español e inglés.
  5. Si existe el secreto STRIPE_SECRET_KEY: crea solo el enlace de pago de Stripe
     de cada producto (con selector de talla) y lee las ventas reales.
  6. Actualiza el cambio USD/EUR con el Banco Central Europeo.
  7. Puntúa, reordena el catálogo y pone etiquetas reales
     ("Nº 1 en Ventas", "Edición Limitada · Últimas unidades", "Alta demanda", "Novedad").
  8. Control de fidelidad visual: separa fotos originales y fotos con modelo; sin foto tuya
     en assets/verified/ solo se muestran las originales (visual_verified = false).
  9. Detecta la campaña de la época (Black Week, Gala de Invierno, Rebajas...) y calcula
     ofertas solo en productos con margen suficiente.
 10. Genera las recomendaciones "Completa el Look".
 11. Guarda data/products.json, data/campaign.json, data/store_analytics.json y data/config.json.

Las claves NUNCA se escriben aquí: van en GitHub → Settings → Secrets and variables → Actions.
"""

import csv
import glob
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dz_common import env, load_json as _lj, printful, printify, printify_shop_id, request, save_json as _sj, stripe  # noqa: E402
import ai_vision  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
NOW = datetime.now(timezone.utc)
TODAY = NOW.strftime("%Y-%m-%d")


# ----------------------------------------------------------------- utilidades
def load_json(name, default):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(name, obj):
    path = os.path.join(DATA, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def log(msg):
    print(f"[motor] {msg}", flush=True)


def slug(text):
    text = text.lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n"), ("ü", "u")):
        text = text.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def pick(seed, options):
    """Elige siempre la misma opción para el mismo producto (no cambia cada día)."""
    h = int(hashlib.md5(seed.encode("utf-8")).hexdigest(), 16)
    return options[h % len(options)]


def http_json(url, headers=None, data=None, method=None, timeout=30):
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data, doseq=True).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("User-Agent", "DAZMOND-engine/1.0")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ------------------------------------------------- reglas de clasificación
# (palabras clave, subcategoría hombre, subcategoría mujer, nombre ES, nombre EN)
FOOTWEAR = [
    (r"slipper|house shoe|pantufla|zapatilla de casa|zapatillas de casa", "zapatillas-hogar", "zapatillas-casa", "Zapatillas de Casa", "House Slippers"),
    (r"boot|bota|botin|botín|chelsea", "botas-botines-hombre", "botas-botines", "Botas", "Boots"),
    (r"clog|zueco", "zuecos-hombre", "zuecos", "Zuecos", "Clogs"),
    (r"sandal|flip ?-?flop|slide|chancla|sandalia", "sandalias-chanclas-hombre", "sandalias-chanclas", "Sandalias", "Sandals"),
    (r"oxford|loafer|derby|brogue|dress shoe|mocasin|mocasín|zapato", "zapatos-elegantes", "zapatillas", "Zapatos", "Dress Shoes"),
    (r"sneaker|trainer|high ?top|low ?top|running shoe|shoe|zapatilla", "zapatillas-hombre", "zapatillas", "Zapatillas", "Sneakers"),
]

DECOR = r"pillow|cushion|cojin|cojín|mug|taza|poster|canvas|lienzo|blanket|manta|candle|vela|\brugs?\b|alfombra|tapestry|wall art|cuadro|puzzle|coaster|posavasos|phone case|funda|towel|toalla|throw pillow|throw blanket|home decor|wall decor"

DECOR_TYPES = [
    (r"pillow|cushion|coj[ií]n", "Cojín", "Cushion"),
    (r"blanket|manta|throw", "Manta", "Throw"),
    (r"mug|taza", "Taza", "Mug"),
    (r"poster|canvas|lienzo|wall art|cuadro|tapestry", "Lámina", "Art Print"),
    (r"candle|vela", "Vela", "Candle"),
    (r"rug|alfombra", "Alfombra", "Rug"),
    (r"towel|toalla", "Toalla", "Towel"),
    (r"phone case|funda", "Funda", "Case"),
]

APPAREL = [
    (r"\btie\b|necktie|corbata", "corbatas", None, "Corbata", "Tie"),
    (r"\bbelt\b|cinturon|cinturón", "cinturon", None, "Cinturón", "Belt"),
    (r"blazer|suit jacket|americana", "americana-blazer", "chaqueta-sudadera-sueter", "Blazer", "Blazer"),
    (r"robe|bathrobe|pajama|pyjama|sleepwear|albornoz|pijama|\bbata\b", "albornoz-pijama", "batas-pijamas", "Pijama", "Loungewear"),
    (r"swim|trunks|bikini|bañador|banador|traje de baño", "banador", "trajes-bano", "Bañador", "Swimwear"),
    (r"underwear|boxer|brief|\bbra\b|lingerie|calzoncillo|sujetador|thong", "ropa-interior", "ropa-interior", "Ropa Interior", "Underwear"),
    (r"bodysuit|\bbody\b", None, "bodies", "Body", "Bodysuit"),
    (r"jumpsuit|romper|overall|dungaree|\bmono\b|\bpeto\b", "conjuntos", "vestidos-monos-petos", "Mono", "Jumpsuit"),
    (r"\bdress\b(?! shirt)|vestido", None, "vestidos-monos-petos", "Vestido", "Dress"),
    (r"skirt|falda", None, "falda", "Falda", "Skirt"),
    (r"legging", "mallas", "leggings", "Leggings", "Leggings"),
    (r"tights|compression|\bmalla", "mallas", "leggings", "Mallas", "Tights"),
    (r"tracksuit|jogger|sweatpant|chandal|chándal|hoodie|sweatshirt|sudadera", "chandal-sudaderas", "chaqueta-sudadera-sueter", "Sudadera", "Sweatshirt"),
    (r"\bset\b|conjunto|co-ord|two piece|2 piece", "conjuntos", "conjuntos", "Conjunto", "Set"),
    (r"coat|jacket|sweater|cardigan|parka|bomber|puffer|abrigo|chaqueta|su[eé]ter|jersey|knit", "abrigo-chaqueta-sueter", "chaqueta-sudadera-sueter", "Abrigo", "Coat"),
    (r"polo", "polos", "polo", "Polo", "Polo"),
    (r"t-shirt|tshirt|\btee\b|camiseta|tank|crop|\btops?\b", "camiseta", "camisetas-blusas-tops", "Camiseta", "Tee"),
    (r"blouse|blusa", None, "camisetas-blusas-tops", "Blusa", "Blouse"),
    (r"shirt|camisa|button", "camisas", "camisas", "Camisa", "Shirt"),
    (r"shorts|bermuda|pantal[oó]n corto", "pantalones-cortos", "pantalones-cortos", "Bermuda", "Shorts"),
    (r"pants|trouser|jean|chino|pantal[oó]n", "pantalones", "pantalones", "Pantalón", "Trousers"),
    (r"\bbag\b|wallet|tote|backpack|purse|clutch|bolso|cartera|mochila", "bolsos-carteras", "bolsos-carteras", "Bolso", "Bag"),
    (r"\bhat\b|\bcap\b|beanie|scarf|bucket|gorro|gorra|bufanda", "gorro-bufanda", "gorros-bufandas", "Gorro", "Hat"),
]

COLORS = {
    "black": ("Negro", "Black"), "white": ("Blanco", "White"), "ivory": ("Marfil", "Ivory"),
    "cream": ("Crema", "Cream"), "beige": ("Beige", "Beige"), "camel": ("Camel", "Camel"),
    "brown": ("Marrón", "Brown"), "navy": ("Azul Marino", "Navy"), "blue": ("Azul", "Blue"),
    "grey": ("Gris", "Grey"), "gray": ("Gris", "Grey"), "red": ("Rojo", "Red"),
    "burgundy": ("Burdeos", "Burgundy"), "green": ("Verde", "Green"), "olive": ("Oliva", "Olive"),
    "gold": ("Oro", "Gold"), "champagne": ("Champán", "Champagne"), "pink": ("Rosa", "Pink"),
    "silver": ("Plata", "Silver"), "purple": ("Púrpura", "Purple"),
}

MATERIALS = {
    "wool": ("lana", "wool"), "cotton": ("algodón", "cotton"), "silk": ("seda", "silk"),
    "satin": ("satén", "satin"), "leather": ("piel", "leather"), "linen": ("lino", "linen"),
    "velvet": ("terciopelo", "velvet"), "cashmere": ("cachemira", "cashmere"),
    "canvas": ("lona", "canvas"), "polyester": ("tejido técnico", "technical fabric"),
    "fleece": ("felpa suave", "soft fleece"), "denim": ("denim", "denim"),
}

LINES = ["Manhattan", "Noir", "Riviera", "Atelier", "Soho", "Imperial", "Avenue", "Monogram", "Hudson", "Madison"]


def detect_gender(row, text):
    g = (row.get("gender") or "").strip().lower()
    if g in ("women", "woman", "mujer", "female", "f", "w"):
        return "women"
    if g in ("men", "man", "hombre", "male", "m"):
        return "men"
    if g in ("unisex", "u"):
        return "unisex"
    if re.search(r"\b(women|woman|womens|women's|ladies|mujer|female)\b", text):
        return "women"
    if re.search(r"\b(men|man|mens|men's|hombre|male)\b", text):
        return "men"
    return "unisex"


def classify(row):
    """Devuelve (lista_de_categorías, nombre ES, nombre EN, necesita_revisión)."""
    text = " ".join([row.get("type", ""), row.get("title", "")]).lower()
    gender = detect_gender(row, text)

    for pattern, men_sub, women_sub, n_es, n_en in FOOTWEAR:
        if re.search(pattern, text):
            if gender == "women":
                return [f"calzado-mujer/{women_sub}"], n_es, n_en, False
            if gender == "men":
                return [f"calzado-hombre/{men_sub}"], n_es, n_en, False
            return [f"calzado-hombre/{men_sub}", f"calzado-mujer/{women_sub}"], n_es, n_en, False

    if re.search(DECOR, text):
        for pat, n_es, n_en in DECOR_TYPES:
            if re.search(pat, text):
                return ["decor"], n_es, n_en, False
        return ["decor"], "Objeto Maison", "Maison Object", False

    for pattern, men_sub, women_sub, n_es, n_en in APPAREL:
        if re.search(pattern, text):
            cats = []
            if gender in ("men", "unisex") and men_sub:
                cats.append(f"hombre/{men_sub}")
            if gender in ("women", "unisex") and women_sub:
                cats.append(f"mujer/{women_sub}")
            if not cats:  # p. ej. corbata marcada "mujer": se queda en la única categoría que existe
                cats.append(f"hombre/{men_sub}" if men_sub else f"mujer/{women_sub}")
            return cats, n_es, n_en, False

    fallback = "mujer/camisetas-blusas-tops" if gender == "women" else "hombre/camiseta"
    return [fallback], "Pieza", "Piece", True


# ------------------------------------------------------------- precios
def compute_price(cost, cats, cfg, old=None):
    p = cfg["pricing"]
    group = cats[0].split("/")[0]
    markup = p["markup_by_menu"].get(group, p["default_markup"])
    if old and old.get("markup_adjust"):
        markup = markup * old["markup_adjust"]
    raw = max(cost * markup, cost + p["min_profit_usd"])
    step = p.get("round_to", 5) or 1
    price = math.ceil(raw / step) * step
    margin = round((price - cost) / price * 100, 1) if price else 0
    return float(price), margin


# ------------------------------------------------------------- textos de lujo
def luxury_copy(pid, n_es, n_en, row):
    text = (row.get("title", "") + " " + row.get("color", "") + " " + row.get("material", "")).lower()
    color = next((v for k, v in COLORS.items() if k in text), None)
    material = next((v for k, v in MATERIALS.items() if k in text), None)
    line = pick(pid + "line", LINES)

    name_es = f"{n_es} {line}" + (f" · {color[0]}" if color else "")
    name_en = f"{line} {n_en}" + (f" · {color[1]}" if color else "")

    mat_es = f" en {material[0]}" if material else ""
    mat_en = f" in {material[1]}" if material else ""

    es = pick(pid + "es", [
        f"Una pieza pensada para quien no sigue tendencias: las crea. Confeccionada bajo demanda{mat_es}, con un acabado impecable y una silueta que habla por sí sola.",
        f"Elegancia americana sin concesiones. Cada unidad{mat_es} se produce solo cuando la pides, para que lo que vistas sea tan exclusivo como tú.",
        f"Líneas limpias, presencia rotunda. Una creación de la casa DAZMOND & Co.{mat_es}, diseñada para acompañarte del día a la noche.",
    ])
    en = pick(pid + "en", [
        f"For those who don't follow trends — they set them. Made to order{mat_en}, with an impeccable finish and a silhouette that speaks for itself.",
        f"Uncompromising American elegance. Each piece{mat_en} is produced only when you order it, so what you wear is as exclusive as you are.",
        f"Clean lines, commanding presence. A DAZMOND & Co. house creation{mat_en}, designed to take you from day to night.",
    ])
    return name_es, name_en, es, en


# ---------------------------------------------------------- lectura proveedores
def read_csvs():
    rows = []
    for path in sorted(glob.glob(os.path.join(DATA, "suppliers", "*.csv"))):
        with open(path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
                if r.get("title") and r.get("supplier"):
                    rows.append(r)
        log(f"CSV leído: {os.path.basename(path)}")
    return rows


def _split_by_color(variants, color_of, size_of):
    groups = {}
    for v in variants:
        groups.setdefault(color_of(v) or "", []).append(v)
    return groups


def read_printify():
    """Lee tu tienda de Printify. Cada color se convierte en una pieza propia (como en las grandes casas)."""
    shop = printify_shop_id()
    if not env("PRINTIFY_TOKEN") or not shop:
        return []
    rows, page = [], 1
    try:
        while page <= 30:
            data = printify(f"/shops/{shop}/products.json?limit=50&page={page}")
            for p in data.get("data", []):
                opt_type = {}
                for o in p.get("options", []):
                    for val in o.get("values", []):
                        opt_type[val["id"]] = (o.get("type", ""), val.get("title", ""))
                enabled = [v for v in p.get("variants", []) if v.get("is_enabled") and v.get("is_available", True)]

                def pick_type(v, kind):
                    for oid in v.get("options", []):
                        t, title = opt_type.get(oid, ("", ""))
                        if t == kind:
                            return title
                    return ""

                groups = _split_by_color(enabled, lambda v: pick_type(v, "color"), lambda v: pick_type(v, "size"))
                for color, vs in groups.items():
                    vids = {v["id"] for v in vs}
                    sizes, vmap = [], {}
                    for v in vs:
                        s = pick_type(v, "size") or (v.get("title") or "").split(" / ")[-1].strip()
                        if s and s not in vmap:
                            sizes.append(s)
                            vmap[s] = v["id"]
                    if not vmap:
                        vmap["unica"] = vs[0]["id"]
                    imgs = [i["src"] for i in p.get("images", []) if vids & set(i.get("variant_ids", []))]
                    imgs = imgs or [i["src"] for i in p.get("images", [])]
                    rows.append({
                        "supplier": "Printify",
                        "supplier_id": f"{p['id']}-{slug(color)}" if color and len(groups) > 1 else str(p["id"]),
                        "supplier_product_id": str(p["id"]),
                        "title": p.get("title", ""), "type": " ".join(p.get("tags", [])),
                        "gender": "", "color": color, "material": "",
                        "cost_usd": str(min(v.get("cost", 0) for v in vs) / 100), "shipping_usd": "6",
                        "sizes": "|".join(sizes[:30]), "images": "|".join(imgs[:8]),
                        "variant_map": vmap,
                        "description": re.sub(r"<[^>]+>", " ", p.get("description", ""))[:600],
                    })
            if page >= data.get("last_page", 1):
                break
            page += 1
        log(f"Printify: {len(rows)} piezas leídas")
    except Exception as e:  # nunca romper el motor por un proveedor
        log(f"Printify no disponible hoy: {str(e)[:200]}")
    return rows


def read_printful():
    """Lee tu tienda de Printful (tienda de tipo API / pedidos manuales)."""
    if not env("PRINTFUL_TOKEN"):
        return []
    cache = load_json("supplier_cache.json", {})
    costs = cache.setdefault("printful_costs", {})
    rows = []
    try:
        offset = 0
        while offset < 2000:
            lst = printful(f"/store/products?limit=100&offset={offset}")
            items = lst.get("result", [])
            for it in items:
                det = printful(f"/store/products/{it['id']}").get("result", {})
                sp = det.get("sync_product", {})
                vs = [v for v in det.get("sync_variants", []) if not v.get("is_ignored")]
                groups = _split_by_color(vs, lambda v: v.get("color") or "", lambda v: v.get("size") or "")
                for color, gv in groups.items():
                    sizes, vmap, imgs, cost = [], {}, [], None
                    for v in gv:
                        s = v.get("size") or "unica"
                        if s not in vmap:
                            sizes.append(s)
                            vmap[s] = v["id"]
                        for fl in v.get("files", []):
                            if fl.get("type") == "preview" and fl.get("preview_url") and fl["preview_url"] not in imgs:
                                imgs.append(fl["preview_url"])
                        cid = str(v.get("variant_id"))
                        if cid not in costs:
                            try:
                                costs[cid] = float(printful(f"/products/variant/{cid}")["result"]["variant"]["price"])
                                time.sleep(0.6)
                            except Exception:
                                costs[cid] = 0
                        c = costs.get(cid) or 0
                        cost = c if cost is None else min(cost, c)
                    if not imgs and sp.get("thumbnail_url"):
                        imgs.append(sp["thumbnail_url"])
                    rows.append({
                        "supplier": "Printful",
                        "supplier_id": f"{sp.get('id')}-{slug(color)}" if color and len(groups) > 1 else str(sp.get("id")),
                        "supplier_product_id": str(sp.get("id")),
                        "title": sp.get("name", ""), "type": (gv[0].get("product") or {}).get("name", ""),
                        "gender": "", "color": color, "material": "",
                        "cost_usd": str(cost or 0), "shipping_usd": "7",
                        "sizes": "|".join(sizes[:30]), "images": "|".join(imgs[:8]),
                        "variant_map": vmap, "description": "",
                    })
                time.sleep(0.6)
            if len(items) < 100:
                break
            offset += 100
        save_json("supplier_cache.json", cache)
        log(f"Printful: {len(rows)} piezas leídas")
    except Exception as e:
        log(f"Printful no disponible hoy: {str(e)[:200]}")
    return rows


# ---------------------------------------------- visitas reales (Firebase, opcional)
def read_firebase_stats(cfg, stats):
    """Lee las visitas y clics que la web guarda en Firebase (colección 'stats', un documento por día)."""
    fb = cfg.get("firebase", {})
    if not fb.get("apiKey") or not fb.get("projectId"):
        return
    base = (f"https://firestore.googleapis.com/v1/projects/{fb['projectId']}/databases/(default)/documents/stats/")
    totals = {}
    for back in range(30):
        day = (NOW - timedelta(days=back)).strftime("%Y%m%d")
        try:
            doc = request(f"{base}{day}?key={fb['apiKey']}", timeout=20, retries=0)
        except Exception:
            continue
        for kind in ("views", "clicks"):
            fields = (((doc.get("fields") or {}).get(kind) or {}).get("mapValue") or {}).get("fields") or {}
            for pid, v in fields.items():
                n = int(v.get("integerValue", 0) or 0)
                t = totals.setdefault(pid, {"views": 0, "clicks": 0, "views_today": 0})
                t[kind] += n
                if back == 0 and kind == "views":
                    t["views_today"] = n
    for pid, t in totals.items():
        stats.setdefault(pid, {}).update(t)
    if totals:
        log(f"Firebase: visitas leídas de {len(totals)} piezas")


# --------------------------------------------------------- cupones (Stripe)
def ensure_coupons(cfg):
    """Crea en Stripe los códigos de descuento de config.json (solo si aún no existen)."""
    if not env("STRIPE_SECRET_KEY"):
        return
    for c in cfg.get("coupons", []):
        code = c.get("code")
        if not code or c.get("created"):
            continue
        try:
            found = stripe(f"/promotion_codes?code={urllib.parse.quote(code)}&limit=1").get("data", [])
            if not found:
                coupon = stripe("/coupons", {"percent_off": c.get("percent_off", 10), "duration": "once",
                                             "name": c.get("name", code)})
                form = {"coupon": coupon["id"], "code": code}
                if c.get("first_order_only"):
                    form["restrictions[first_time_transaction]"] = "true"
                stripe("/promotion_codes", form)
                log(f"Cupón creado en Stripe: {code}")
            c["created"] = True
        except Exception as e:
            log(f"Cupón {code} no creado hoy: {str(e)[:160]}")


# ------------------------------------------------------------------ Stripe
STRIPE = "https://api.stripe.com/v1"
COUNTRIES = ("US CA MX ES FR DE IT PT GB IE NL BE LU AT CH SE NO DK FI PL CZ GR RO HU "
             "AU NZ JP KR SG HK AE SA IL BR AR CL CO PE UY ZA").split()


def stripe_call(path, data=None, method=None):
    key = os.environ.get("STRIPE_SECRET_KEY")
    return http_json(f"{STRIPE}{path}", {"Authorization": f"Bearer {key}"}, data, method)


def _create_link(p, cents, tag):
    """Crea un precio y un enlace de pago en Stripe. Devuelve (url, id)."""
    if not p.get("stripe_product_id"):
        prod = stripe_call("/products", {"name": p["name_en"][:250], "metadata[dazmond_id]": p["id"],
                                         "images[]": p.get("display_images", [])[:1]})
        p["stripe_product_id"] = prod["id"]
    price = stripe_call("/prices", {"product": p["stripe_product_id"], "unit_amount": cents, "currency": "usd"})
    data = {
        "line_items[0][price]": price["id"],
        "line_items[0][quantity]": 1,
        "line_items[0][adjustable_quantity][enabled]": "true",
        "line_items[0][adjustable_quantity][minimum]": 1,
        "line_items[0][adjustable_quantity][maximum]": 10,
        "metadata[dazmond_id]": p["id"],
        "metadata[tipo]": tag,
        "shipping_address_collection[allowed_countries][]": COUNTRIES,
        "phone_number_collection[enabled]": "true",
        "allow_promotion_codes": "true",
        "after_completion[type]": "redirect",
        "after_completion[redirect][url]": f"{SITE}/#/gracias",
    }
    sizes = [s for s in p.get("sizes", []) if re.sub(r"[^A-Za-z0-9]", "", s)][:200]
    p["size_values"] = {re.sub(r"[^A-Za-z0-9]", "", s) or f"t{i}": s for i, s in enumerate(sizes)}
    if sizes:
        data["custom_fields[0][key]"] = "talla"
        data["custom_fields[0][type]"] = "dropdown"
        data["custom_fields[0][label][type]"] = "custom"
        data["custom_fields[0][label][custom]"] = "Talla / Size"
        for i, s in enumerate(sizes):
            data[f"custom_fields[0][dropdown][options][{i}][label]"] = s
            data[f"custom_fields[0][dropdown][options][{i}][value]"] = re.sub(r"[^A-Za-z0-9]", "", s) or f"t{i}"
    link = stripe_call("/payment_links", data)
    return link["url"], link["id"]


def _deactivate(link_id):
    if link_id:
        try:
            stripe_call(f"/payment_links/{link_id}", {"active": "false"})
        except Exception:
            pass


SITE = "https://dazmond.com"


def detect_site(cfg):
    """Usa dazmond.com en cuanto el dominio apunte a esta tienda; mientras tanto, la dirección de GitHub."""
    global SITE
    domain = cfg.get("domain", "dazmond.com")
    try:
        data = request(f"https://{domain}/data/catalog.json", timeout=15, retries=0)
        if isinstance(data, dict) and data.get("menus"):
            SITE = f"https://{domain}"
        else:
            raise ValueError
    except Exception:
        SITE = cfg.get("pages_url", "https://ivanjalda.github.io/dazmond-store")
    cfg["site_url"] = SITE
    log(f"Dirección de la tienda: {SITE}")


def ensure_payment_link(p):
    """Enlace normal y, si hay oferta de campaña, un enlace aparte con el precio de oferta."""
    if not os.environ.get("STRIPE_SECRET_KEY") or str(p.get("supplier_id", "")).upper().startswith("DEMO-"):
        return
    try:
        cents = int(round(p["price_usd"] * 100))
        fresh = p.get("stripe_payment_link") and p.get("stripe_price_cents") == cents and p.get("stripe_site") == SITE
        if not p.get("manual_payment_link") and not fresh:
            url, lid = _create_link(p, cents, "normal")
            _deactivate(p.get("stripe_link_id"))
            p["stripe_payment_link"], p["stripe_link_id"], p["stripe_price_cents"] = url, lid, cents
            p["stripe_site"] = SITE
            p.pop("stripe_offer_cents", None)  # el de oferta también se renueva con la nueva dirección
            log(f"Stripe: enlace normal listo para {p['id']}")
        offer = p.get("offer")
        old_offer_id = p.get("stripe_offer_link_id")
        if offer:
            oc = int(round(offer["price_usd"] * 100))
            if p.get("stripe_offer_cents") != oc or not p.get("stripe_offer_link"):
                url, lid = _create_link(p, oc, "oferta")
                _deactivate(old_offer_id)
                p["stripe_offer_link"], p["stripe_offer_link_id"], p["stripe_offer_cents"] = url, lid, oc
                log(f"Stripe: enlace de oferta listo para {p['id']}")
            offer["payment_link"] = p["stripe_offer_link"]
        elif old_offer_id:  # terminó la campaña: se desactiva el enlace de oferta
            _deactivate(old_offer_id)
            for k in ("stripe_offer_link", "stripe_offer_link_id", "stripe_offer_cents"):
                p.pop(k, None)
    except Exception as e:
        log(f"Stripe: no se pudo preparar el enlace de {p['id']}: {e}")


def stripe_sales(link_to_pid):
    """Cuenta ventas de los últimos 30 días y de hoy, por producto."""
    out = {}
    if not os.environ.get("STRIPE_SECRET_KEY"):
        return out
    since = int((NOW - timedelta(days=30)).timestamp())
    start_today = int(NOW.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    after, pages = None, 0
    try:
        while pages < 30:
            q = f"/checkout/sessions?limit=100&status=complete&created[gte]={since}"
            if after:
                q += f"&starting_after={after}"
            data = stripe_call(q)
            for s in data.get("data", []):
                pid = (s.get("metadata") or {}).get("dazmond_id") or link_to_pid.get(s.get("payment_link"))
                if not pid:
                    continue
                o = out.setdefault(pid, {"sales_30d": 0, "sales_today": 0, "revenue_30d": 0.0})
                o["sales_30d"] += 1
                o["revenue_30d"] += (s.get("amount_total") or 0) / 100
                if s.get("created", 0) >= start_today:
                    o["sales_today"] += 1
            if not data.get("has_more") or not data.get("data"):
                break
            after = data["data"][-1]["id"]
            pages += 1
        log(f"Stripe: ventas leídas de {len(out)} productos")
    except Exception as e:
        log(f"Stripe: no se pudieron leer las ventas: {e}")
    return out


# ------------------------------------------------------------ divisa (BCE)
def update_fx(cfg):
    try:
        req = urllib.request.Request("https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml",
                                     headers={"User-Agent": "DAZMOND-engine/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            tree = ET.fromstring(r.read())
        for el in tree.iter():
            if el.attrib.get("currency") == "USD":
                usd_per_eur = float(el.attrib["rate"])
                cfg["fx"]["EUR"] = round(1 / usd_per_eur, 4)
                cfg["fx"]["updated"] = TODAY
                log(f"Cambio actualizado: 1 USD = {cfg['fx']['EUR']} EUR")
                return
    except Exception as e:
        log(f"Cambio no actualizado hoy (se mantiene el anterior): {e}")


# ----------------------------------------- auto-edición de cada producto (IA)
def ai_rewrite(plist, cfg):
    """Reescribe con la IA, una sola vez por producto, nombre + descripción + detalles en ES/EN."""
    if not ai_vision.ready():
        return
    limit = cfg.get("ai", {}).get("copy_per_run", 40)
    todo = [p for p in plist if not p.get("ai_copy") and not p.get("locked_copy")
            and not str(p.get("supplier_id", "")).upper().startswith("DEMO-")]
    done = 0
    for p in todo[:limit]:
        img = next((os.path.join(ROOT, u) for u in p.get("display_images", []) if not u.startswith("http")), None)
        out = ai_vision.write_copy(p.get("source_text") or p.get("source_title", ""), img)
        if out:
            p["name_es"], p["name_en"] = out["name_es"].strip(), out["name_en"].strip()
            p["desc_es"], p["desc_en"] = out["desc_es"].strip(), out["desc_en"].strip()
            p["details_es"], p["details_en"] = out.get("details_es", "").strip(), out.get("details_en", "").strip()
            p["ai_copy"] = True
            done += 1
        time.sleep(4)  # respeta el límite gratuito
    if todo:
        log(f"IA: {done} productos reescritos con el estilo de la casa · quedan {max(0, len(todo) - done)}")


# ------------------------------------------------ fotos y verificación visual
VERIFIED_DIR = os.path.join(ROOT, "assets", "verified")
MODEL_HINT = re.compile(r"model|lifestyle|person|people|wearing|on-body|onbody|ai-gen|generated", re.I)


def verified_photos(pid):
    """Fotos que tú has revisado y subido a assets/verified/ (ej.: printful-demo-001-1.jpg)."""
    if not os.path.isdir(VERIFIED_DIR):
        return []
    files = []
    for f in sorted(os.listdir(VERIFIED_DIR)):
        name, ext = os.path.splitext(f)
        if ext.lower() in (".jpg", ".jpeg", ".png", ".webp") and (name == pid or name.startswith(pid + "-")):
            files.append(f"assets/verified/{f}")
    return files


def sort_images(p, row_images, row_model_images):
    """Separa fotos originales nítidas de fotos con modelo/generadas y decide cuáles se ven."""
    originals, models = [], []
    for u in row_images:
        (models if MODEL_HINT.search(u) else originals).append(u)
    models += row_model_images
    if p.get("_shop_photos"):  # fotos que elegiste en Shopify para este producto
        originals = list(dict.fromkeys(p.pop("_shop_photos") + originals))
    p["images_original"] = originals or p.get("images_original", [])
    p["images_model"] = models or p.get("images_model", [])
    ver = verified_photos(p["id"])
    if p.get("shop_verified"):  # fotos elegidas por ti (Shopify o bot): ya están revisadas
        p["visual_verified"] = True
        p["display_images"] = ver + [u for u in row_images + row_model_images if u not in ver] or ver + p["images_original"] + p["images_model"]
        return
    p["visual_verified"] = bool(ver)
    # Regla de fidelidad: sin foto verificada, solo se muestran las originales nítidas.
    p["display_images"] = ver + p["images_original"] if ver else list(p["images_original"])


# ----------------------------------------------------- campañas por temporada
def black_week(year):
    nov1 = datetime(year, 11, 1).date()
    first_thu = nov1 + timedelta(days=(3 - nov1.weekday()) % 7)
    black_friday = first_thu + timedelta(days=22)
    return black_friday - timedelta(days=4), black_friday + timedelta(days=3)  # lunes → Cyber Monday


def active_campaign(cfg, today):
    md = today.strftime("%m-%d")
    found = []
    for c in cfg.get("campaigns", []):
        if c.get("rule") == "black_week":
            s, e = black_week(today.year)
            if s <= today <= e:
                found.append(dict(c, ends=e.isoformat()))
            continue
        s, e = c.get("start"), c.get("end")
        if not s or not e:
            continue
        inside = s <= md <= e if s <= e else (md >= s or md <= e)
        if inside:
            year = today.year + (1 if s > e and md >= s else 0)
            found.append(dict(c, ends=f"{year}-{e}"))
    return sorted(found, key=lambda c: c.get("priority", 9))[0] if found else None


def record_price(p):
    hist = p.setdefault("price_history", {})
    hist[TODAY] = p["price_usd"]
    limit = (NOW - timedelta(days=35)).strftime("%Y-%m-%d")
    for d in [d for d in hist if d < limit]:
        del hist[d]


def apply_offer(p, camp, cfg):
    """Oferta solo si, tras el descuento, se mantiene el margen y el beneficio mínimos.
    El precio tachado es el más bajo de los últimos 30 días (norma europea de rebajas)."""
    p.pop("offer", None)
    if not camp or not camp.get("discount_pct") or not p.get("available") or p.get("no_offers"):
        return
    o = cfg.get("offers", {})
    cost, price = p.get("cost_usd", 0), p["price_usd"]
    if not cost or cost <= 0:  # sin coste conocido no se rebaja: así nunca se vende con pérdidas
        return
    limit = (NOW - timedelta(days=30)).strftime("%Y-%m-%d")
    hist = p.get("price_history", {})
    if len(hist) < o.get("min_days_listed", 14):  # no se rebaja un precio que aún no se ha cobrado de verdad
        return
    ref = min([v for d, v in hist.items() if d >= limit] or [price])
    for pct in range(int(camp["discount_pct"]), 4, -5):
        offer = math.floor(price * (1 - pct / 100))
        if offer <= 0 or offer >= ref:
            continue
        margin = (offer - cost) / offer * 100
        if margin >= o.get("min_margin_after_discount_pct", 45) and offer - cost >= o.get("min_profit_after_discount_usd", 15):
            p["offer"] = {"price_usd": float(offer), "ref_price_usd": ref,
                          "pct": round((ref - offer) / ref * 100), "campaign": camp["id"], "ends": camp.get("ends")}
            return


# ------------------------------------------------------ recomendaciones look
LOOK_ORDER = {
    "hombre": ["abrigo-chaqueta-sueter", "americana-blazer", "camisas", "polos", "camiseta", "pantalones",
               "corbatas", "cinturon", "bolsos-carteras", "gorro-bufanda"],
    "mujer": ["chaqueta-sudadera-sueter", "vestidos-monos-petos", "camisetas-blusas-tops", "camisas", "falda",
              "pantalones", "bolsos-carteras", "gorros-bufandas"],
}


def complete_the_look(products):
    by_id = {p["id"]: p for p in products}
    for p in products:
        main = p["categories"][0]
        group, _, sub = main.partition("/")
        gender = {"calzado-hombre": "hombre", "calzado-mujer": "mujer"}.get(group, group)
        shoes = "calzado-hombre" if gender == "hombre" else "calzado-mujer"
        picks = []
        for q in products:  # ya vienen ordenados por puntuación
            if q["id"] == p["id"] or not q.get("available", True):
                continue
            qg, _, qs = q["categories"][0].partition("/")
            if gender in ("hombre", "mujer"):
                ok = (qg == gender and qs != sub) or (qg == shoes and group != shoes)
            else:  # decor: sugiere otras piezas de decoración
                ok = qg == "decor"
            if ok and all(by_id[x]["categories"][0] != q["categories"][0] for x in picks):
                picks.append(q["id"])
            if len(picks) == 4:
                break
        p["look"] = picks


# --------------------------------------------------------------- programa
def main():
    cfg = load_json("config.json", {})
    detect_site(cfg)
    old_list = load_json("products.json", {"products": []}).get("products", [])
    old = {p["id"]: p for p in old_list}
    analytics = load_json("store_analytics.json", {"products": {}})
    stats = analytics.setdefault("products", {})

    rows = read_csvs() + read_printify() + read_printful()
    products = dict(old)  # nunca se borra nada: lo que ya existía se conserva

    for r in rows:
        for k in ("type", "gender", "color", "material"):  # lo que la IA leyó en las fotos rellena lo que falte
            if r.get(f"ai_{k}"):
                r[k] = (r.get(k, "") + " " + r[f"ai_{k}"]).strip() if k == "type" else (r.get(k) or r[f"ai_{k}"])
        if not r.get("sizes") and r.get("ai_sizes"):
            r["sizes"] = r["ai_sizes"]
        if not r.get("title") and r.get("ai_title"):
            r["title"] = r["ai_title"]
        pid = slug(r["supplier"]) + "-" + slug(r.get("supplier_id") or r["title"])
        prev = old.get(pid, {})
        cats, n_es, n_en, review = classify(r)
        if prev.get("locked_category"):
            cats = prev["categories"]
        try:
            cost = float(r.get("cost_usd") or 0) + float(r.get("shipping_usd") or 0)
        except ValueError:
            cost = 0.0
        p = dict(prev)
        p.update({
            "id": pid, "supplier": r["supplier"], "supplier_id": r.get("supplier_id", ""),
            "source_title": r["title"], "categories": cats, "cost_usd": round(cost, 2),
            "sizes": [s.strip() for s in r.get("sizes", "").split("|") if s.strip()],
            "needs_review": review, "created": prev.get("created", TODAY),
            "material": r.get("material", "") or prev.get("material", ""),
            "color": r.get("color", "") or prev.get("color", ""),
        })
        urls = lambda key: [u.strip() for u in r.get(key, "").split("|") if u.strip().startswith(("http", "assets/"))]
        p["_row_images"], p["_row_model"] = urls("images"), urls("model_images")
        if r.get("variant_map"):
            p["variant_map"] = r["variant_map"]
        if r.get("supplier_product_id"):
            p["supplier_product_id"] = r["supplier_product_id"]
        if r.get("gender"):
            p["gender"] = r["gender"]
        p["shop_verified"] = r.get("verified") == "yes"
        p["size_guide"] = [u for u in (r.get("size_guide") or "").split("|") if u]
        try:
            p["size_chart"] = json.loads(r["size_chart"]) if r.get("size_chart") else p.get("size_chart")
        except ValueError:
            pass
        if r.get("sku_map"):
            try:
                p["sku_map"] = json.loads(r["sku_map"])
            except ValueError:
                pass
        if r.get("description") and r.get("verified") == "yes":
            p["details_src"] = r["description"][:1500]
        if r.get("stock", "") != "":
            try:
                p["stock"] = int(float(r["stock"]))
            except ValueError:
                pass
        if r.get("stripe_payment_link"):
            p["stripe_payment_link"] = r["stripe_payment_link"]
            p["stripe_link_id"] = r.get("stripe_link_id", prev.get("stripe_link_id", ""))
            p["manual_payment_link"] = True
        p["source_text"] = " ".join(x for x in (r.get("title", ""), r.get("type", ""), r.get("color", ""),
                                                 r.get("material", ""), r.get("description", "")) if x)[:3000]
        if not prev.get("locked_copy") and not prev.get("ai_copy"):  # plantilla mientras la IA no lo reescribe
            p["name_es"], p["name_en"], p["desc_es"], p["desc_en"] = luxury_copy(pid, n_es, n_en, r)
        fixed = 0.0
        try:
            fixed = float(r.get("price_usd") or 0)
        except ValueError:
            pass
        if fixed > 0 and not prev.get("locked_price"):  # precio puesto por ti (o el de tu Shopify): se respeta
            p["price_usd"] = float(round(fixed, 2))
            p["margin_pct"] = round((fixed - cost) / fixed * 100, 1) if cost > 0 else None
            p["fixed_price"] = True
        elif not prev.get("locked_price") and cost > 0:
            p["price_usd"], p["margin_pct"] = compute_price(cost, cats, cfg, prev)
        products[pid] = p

    plist = list(products.values())
    # Los 6 productos de ejemplo desaparecen solos en cuanto entra el primer producto real
    real = any(not str(p.get("supplier_id", "")).upper().startswith("DEMO-") for p in plist)
    plist = [p for p in plist if not (real and str(p.get("supplier_id", "")).upper().startswith("DEMO-"))]
    # Si un producto de Printify/Printful llega dos veces (por la conexión directa y por la mudanza de
    # Shopify), se queda la versión conectada (pedidos automáticos) con tus fotos y guía de Shopify.
    api = {}
    for p in plist:
        if p.get("variant_map") and p.get("supplier") in ("Printify", "Printful"):
            api.setdefault((p["supplier"], (p.get("source_title") or "").strip().lower()), p)
    merged = []
    for p in plist:
        key = (p.get("supplier"), (p.get("source_title") or "").strip().lower())
        twin = api.get(key)
        if twin and twin is not p and not p.get("variant_map"):
            for k in ("size_guide", "size_chart", "details_src"):
                if p.get(k) and not twin.get(k):
                    twin[k] = p[k]
            twin["_shop_photos"] = (p.get("_row_images") or []) + (p.get("display_images") or [])
            if p.get("fixed_price") and not twin.get("locked_price"):  # tu precio de Shopify manda
                twin["price_usd"], twin["margin_pct"], twin["fixed_price"] = p["price_usd"], p.get("margin_pct"), True
            continue
        merged.append(p)
    plist = merged
    for p in plist:
        sort_images(p, p.pop("_row_images", []), p.pop("_row_model", []))
        p.setdefault("price_usd", 0.0)
        p["available"] = p["price_usd"] > 0 and p.get("stock", 1) != 0
        if p["price_usd"] > 0:
            record_price(p)
    log(f"Productos en catálogo: {len(plist)} · con foto verificada: {sum(p['visual_verified'] for p in plist)}")

    # Ventas reales (Stripe) → analíticas
    link_to_pid = {}
    for p in plist:
        for k in ("stripe_link_id", "stripe_offer_link_id"):
            if p.get(k):
                link_to_pid[p[k]] = p["id"]
    has_stripe = bool(os.environ.get("STRIPE_SECRET_KEY"))
    sales = stripe_sales(link_to_pid)
    read_firebase_stats(cfg, stats)
    ensure_coupons(cfg)
    for p in plist:
        s = stats.setdefault(p["id"], {})
        for k in ("views", "clicks", "views_today", "sales_30d", "sales_today"):
            s.setdefault(k, 0)
        s["visual_verified"] = p.get("visual_verified", False)
        if has_stripe:
            s.update(sales.get(p["id"], {"sales_30d": 0, "sales_today": 0, "revenue_30d": 0.0}))

    # Puntuación
    for p in plist:
        s = stats[p["id"]]
        days = (NOW.date() - datetime.strptime(p["created"], "%Y-%m-%d").date()).days
        p["score"] = round(s["sales_30d"] * 12 + s["sales_today"] * 20 + s["clicks"] * 0.6 + s["views"] * 0.05
                           + (p.get("margin_pct") or 0) * 0.1 + max(0, 14 - days) * 1.5
                           + (3 if p["visual_verified"] else 0), 2)

    # Ajuste suave de lo que peor rinde (solo si activas "auto_price_adjust": true en config)
    if cfg.get("pricing", {}).get("auto_price_adjust"):
        for p in plist:
            s = stats[p["id"]]
            if s["views"] >= 200 and s["sales_30d"] == 0 and not p.get("locked_price"):
                p["markup_adjust"] = max(0.85, round(p.get("markup_adjust", 1) - 0.03, 2))

    plist.sort(key=lambda p: (not p["available"], -p["score"], p["id"]))

    # Etiquetas (solo con datos reales)
    b = cfg.get("badges", {})
    ranked = sorted([p for p in plist if stats[p["id"]]["sales_30d"] > 0], key=lambda p: -stats[p["id"]]["sales_30d"])
    top = {p["id"]: i for i, p in enumerate(ranked[: b.get("top_seller_rank", 3)])}
    for i, p in enumerate(plist):
        p["rank"] = i + 1
        s = stats[p["id"]]
        badge = None
        if p["id"] in top:
            badge = "top1" if top[p["id"]] == 0 else "bestseller"
        elif isinstance(p.get("stock"), int) and 0 < p["stock"] <= b.get("low_stock_threshold", 5):
            badge = "lowstock"
        elif s.get("views_today", 0) >= b.get("high_demand_views_today", 40) or s["sales_today"] >= 2:
            badge = "hot"
        elif (NOW.date() - datetime.strptime(p["created"], "%Y-%m-%d").date()).days <= 14:
            badge = "new"
        p["badge"] = badge

    # Campaña de temporada + ofertas que protegen el margen
    camp = active_campaign(cfg, NOW.date())
    for p in plist:
        apply_offer(p, camp, cfg)
    campaign = {"active": False, "updated": TODAY}
    if camp:
        eff = lambda p: (p.get("offer") or {}).get("price_usd", p["price_usd"])
        avail = [p for p in plist if p["available"]]
        gifts = [p["id"] for p in avail if eff(p) <= cfg.get("offers", {}).get("gift_max_price_usd", 150)][:8] if camp.get("gifts") else []
        offers = [p["id"] for p in avail if p.get("offer")]
        campaign = {"active": True, "updated": TODAY, "id": camp["id"], "ends": camp.get("ends"),
                    "discount_pct": camp.get("discount_pct", 0), "es": camp["es"], "en": camp["en"],
                    "offers": offers, "gifts": gifts, "selection": (offers + [g for g in gifts if g not in offers]) or [p["id"] for p in avail[:8]]}
        log(f"Campaña activa: {camp['id']} · {len(offers)} ofertas · {len(gifts)} regalos")

    ai_rewrite(plist, cfg)
    complete_the_look(plist)
    for p in plist:
        ensure_payment_link(p)

    update_fx(cfg)
    analytics["updated"] = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    save_json("products.json", {"updated": analytics["updated"], "products": plist})
    save_json("campaign.json", campaign)
    save_json("store_analytics.json", analytics)
    save_json("config.json", cfg)
    review = [p["id"] for p in plist if p.get("needs_review")]
    if review:
        log(f"Revisa la categoría de: {', '.join(review)}")
    log("Listo.")


if __name__ == "__main__":
    sys.exit(main())
