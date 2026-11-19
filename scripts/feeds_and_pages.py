#!/usr/bin/env python3
"""
DAZMOND & Co. — Escaparates gratis en todas partes (solo Python estándar).

Genera solo, cada hora:
  1. Una página propia para cada pieza:  /p/<código>/   (con precio, fotos y botón de compra).
     Google, Bing, Pinterest, Meta y TikTok exigen una página así para aceptar cada producto.
  2. Catálogos de productos (los "feeds") que esos sitios leen solos cada día:
       feeds/catalogo-en.xml   (inglés)    feeds/catalogo-es.xml   (español)
     Sirven para: Google Shopping y YouTube, Microsoft Bing, Pinterest, Facebook/Instagram y TikTok.
  3. sitemap.xml y robots.txt para que Google encuentre todas las páginas.
"""

import html
import json
import os
import shutil
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dz_common import NOW, ROOT, TODAY, load_json  # noqa: E402

GENDER = {"hombre": "male", "mujer": "female", "calzado-hombre": "male", "calzado-mujer": "female"}
GOOGLE_CAT = {"calzado-hombre": "187", "calzado-mujer": "187", "decor": "696"}  # Calzado · Decoración


def esc(s):
    return html.escape(str(s or ""), quote=True)


def absolute(site, url):
    return url if url.startswith("http") else f"{site}/{url.lstrip('/')}"


def names(catalog):
    out = {}
    for m in catalog.get("menus", []):
        for g in m.get("groups", []):
            for it in g.get("items", []):
                out[f"{g['id']}/{it['id']}"] = (f"{m['es']} > {it['es']}", f"{m['en']} > {it['en']}")
        if m.get("direct"):
            out[m["direct"]] = (m["es"], m["en"])
    return out


def visible(products):
    return [p for p in products if p.get("available") and p.get("price_usd", 0) > 0 and p.get("display_images")
            and not str(p.get("supplier_id", "")).upper().startswith("DEMO-")]


# ------------------------------------------------------------ página de cada pieza
def product_page(p, site, cat_names):
    name_es, name_en = p.get("name_es", ""), p.get("name_en", "")
    price = (p.get("offer") or {}).get("price_usd", p["price_usd"])
    imgs = [absolute(site, u) for u in p.get("display_images", [])]
    cat = cat_names.get(p["categories"][0], ("", ""))
    url = f"{site}/p/{p['id']}/"
    ld = {
        "@context": "https://schema.org", "@type": "Product", "name": name_en, "image": imgs[:5],
        "description": p.get("desc_en", "")[:500], "sku": p["id"], "brand": {"@type": "Brand", "name": "DAZMOND & Co."},
        "category": cat[1],
        "offers": {"@type": "Offer", "url": url, "priceCurrency": "USD", "price": f"{price:.2f}",
                   "availability": "https://schema.org/InStock", "itemCondition": "https://schema.org/NewCondition"},
    }
    sizes = " · ".join(esc(s) for s in p.get("sizes", []))
    buy = (p.get("offer") or {}).get("payment_link") or p.get("stripe_payment_link") or ""
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{esc(name_es)} — DAZMOND &amp; Co.</title>
<meta name="description" content="{esc(p.get('desc_es', '')[:155])}">
<link rel="canonical" href="{url}">
<meta property="og:type" content="product">
<meta property="og:title" content="{esc(name_es)} — DAZMOND &amp; Co.">
<meta property="og:description" content="{esc(p.get('desc_es', '')[:155])}">
<meta property="og:image" content="{esc(imgs[0] if imgs else '')}">
<meta property="og:url" content="{url}">
<meta property="product:price:amount" content="{price:.2f}">
<meta property="product:price:currency" content="USD">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#000000">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,400;0,500;1,300;1,400&family=Inter:wght@300;400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="../../assets/style.css">
<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>
<script>window.DZ_BASE = "../../"; window.DZ_PRODUCT = "{esc(p['id'])}";</script>
</head>
<body>
<div class="topbar"><div class="topbar-msg" id="topMsg"></div><div class="topbar-switches"><button class="switch" id="langBtn">ES | EN</button><button class="switch" id="curBtn">USD $</button></div></div>
<header class="header">
  <div class="header-left"><button class="icon-btn" id="searchBtn" aria-label="Buscar"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg></button><button class="icon-btn" id="menuBtn" aria-label="Menú"><svg viewBox="0 0 24 24"><path d="M4 8h16M4 16h16"/></svg></button></div>
  <a href="../../" class="logo">Dazmond <span>&amp;</span> Co.</a>
  <div class="header-right"><a href="../../#/cuenta" class="icon-btn" aria-label="Mi cuenta"><svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/></svg><span class="count" id="favCount"></span></a><button class="icon-btn" id="cartBtn" aria-label="Carrito"><svg viewBox="0 0 24 24"><path d="M5 8h14l-1.2 12H6.2z"/><path d="M9 8V6a3 3 0 016 0v2"/></svg><span class="count" id="cartCount"></span></button></div>
</header>
<nav class="mainnav" id="mainNav"></nav><div class="megamenu" id="megaMenu" hidden></div>
<main id="app" tabindex="-1">
  <section class="section"><div class="pdp"><div class="gallery">{''.join(f'<div class="shot"><img src="{esc(u)}" alt="{esc(name_es)}"></div>' for u in imgs[:6])}</div>
  <div class="pdp-info"><p class="crumbs">{esc(cat[0])}</p><h1>{esc(name_es)}</h1><div class="pdp-price">{price:,.0f} $</div>
  <p class="note">{sizes}</p>{f'<a class="btn btn-solid btn-block" href="{esc(buy)}">Comprar ahora</a>' if buy else ''}
  <p class="muted" style="margin-top:20px;white-space:pre-line">{esc(p.get('desc_es', ''))}</p>
  <p class="muted" style="margin-top:20px">{esc(name_en)} — {esc(p.get('desc_en', '').split(chr(10))[0])}</p></div></div></section>
</main>
<section class="subscribe" id="subscribe"><p class="eyebrow" data-i18n="sub_eyebrow"></p><h2 data-i18n="sub_title"></h2><p class="muted" data-i18n="sub_text"></p>
<form id="subForm" class="sub-form"><input type="email" name="EMAIL" required autocomplete="email" data-i18n-ph="sub_ph"><button type="submit" class="btn" data-i18n="sub_btn"></button></form>
<p class="sub-msg" id="subMsg" role="status"></p><iframe name="subFrame" id="subFrame" hidden title="subscribe"></iframe></section>
<footer class="footer"><div class="footer-logo">Dazmond <span>&amp;</span> Co.</div><nav class="social" id="social"></nav>
<div class="footer-cols"><div><h4 data-i18n="f_house"></h4><a href="../../#/identidad" data-i18n="f_identity"></a><a href="../../#/c/decor" data-i18n="f_decor"></a></div>
<div><h4 data-i18n="f_help"></h4><a href="../../#/legal/envios" data-i18n="l_envios"></a><a href="../../#/legal/devoluciones" data-i18n="l_devoluciones"></a></div>
<div><h4 data-i18n="f_legal"></h4><a href="../../#/legal/aviso" data-i18n="l_aviso"></a><a href="../../#/legal/privacidad" data-i18n="l_privacidad"></a><a href="../../#/legal/terminos" data-i18n="l_terminos"></a></div></div>
<p class="secure" data-i18n="secure"></p><div class="payments"><span>Stripe</span><span>Visa</span><span>Mastercard</span><span>Amex</span><span>PayPal</span><span>Apple Pay</span><span>Google Pay</span></div><p class="copyright" id="copyright"></p></footer>
<div class="overlay" id="searchOverlay" hidden><div class="overlay-inner"><div class="search-row"><input type="search" id="searchInput" data-i18n-ph="search_ph" autocomplete="off"><button class="icon-btn" data-close="searchOverlay">✕</button></div><div class="grid" id="searchResults"></div></div></div>
<aside class="drawer" id="cartDrawer" hidden><div class="drawer-head"><h3 data-i18n="cart"></h3><button class="icon-btn" data-close="cartDrawer">✕</button></div><div id="cartItems" class="drawer-body"></div><div class="drawer-foot" id="cartFoot"></div></aside>
<div class="backdrop" id="backdrop" hidden></div>
<div class="lightbox" id="lightbox" hidden><button class="close" id="lbClose">✕</button><img id="lbImg" alt=""></div>
<script src="../../assets/app.js" defer></script>
</body>
</html>
"""


# --------------------------------------------------------------- catálogos (feeds)
def feed(lang, products, site, cat_names, rate):
    items = []
    for p in products:
        name = p.get(f"name_{lang}", p.get("name_en", ""))
        desc = (p.get(f"desc_{lang}") or p.get("desc_en") or name).replace("\n", " ")
        imgs = [absolute(site, u) for u in p["display_images"]]
        cat = cat_names.get(p["categories"][0], ("", ""))[0 if lang == "es" else 1]
        group = p["categories"][0].split("/")[0]
        gender = GENDER.get(group, "unisex")
        if p.get("gender") in ("men", "women"):
            gender = "male" if p["gender"] == "men" else "female"
        o = p.get("offer")
        sizes = p.get("sizes") or [""]
        for s in sizes:
            iid = f"{p['id']}-{s}" if s else p["id"]
            parts = [
                f"<g:id>{esc(iid[:50])}</g:id>", f"<g:item_group_id>{esc(p['id'][:50])}</g:item_group_id>",
                f"<title>{esc(name[:150])}</title>", f"<description>{esc(desc[:4900])}</description>",
                f"<link>{site}/p/{p['id']}/</link>", f"<g:image_link>{esc(imgs[0])}</g:image_link>",
                *[f"<g:additional_image_link>{esc(u)}</g:additional_image_link>" for u in imgs[1:10]],
                "<g:availability>in_stock</g:availability>", "<g:condition>new</g:condition>",
                f"<g:price>{p['price_usd']:.2f} USD</g:price>",
                "<g:brand>DAZMOND &amp; Co.</g:brand>", "<g:identifier_exists>no</g:identifier_exists>",
                f"<g:product_type>{esc(cat)}</g:product_type>", "<g:age_group>adult</g:age_group>",
                f"<g:gender>{gender}</g:gender>",
            ]
            if group in GOOGLE_CAT:
                parts.append(f"<g:google_product_category>{GOOGLE_CAT[group]}</g:google_product_category>")
            if p.get("color"):
                parts.append(f"<g:color>{esc(p['color'])}</g:color>")
            if s:
                parts.append(f"<g:size>{esc(s)}</g:size>")
            if o:
                start = NOW.strftime("%Y-%m-%dT00:00+0000")
                end = f"{o.get('ends') or (NOW + timedelta(days=7)).strftime('%Y-%m-%d')}T23:59+0000"
                parts.append(f"<g:sale_price>{o['price_usd']:.2f} USD</g:sale_price>")
                parts.append(f"<g:sale_price_effective_date>{start}/{end}</g:sale_price_effective_date>")
            items.append("<item>" + "".join(parts) + "</item>")
    title = "DAZMOND & Co."
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0" xmlns:g="http://base.google.com/ns/1.0">'
            f"<channel><title>{esc(title)}</title><link>{site}</link><description>DAZMOND &amp; Co. — {lang}</description>"
            + "\n".join(items) + "</channel></rss>\n")


def main():
    cfg = load_json("config.json", {})
    site = cfg.get("site_url") or cfg.get("pages_url") or "https://dazmond.com"
    products = load_json("products.json", {"products": []}).get("products", [])
    cat_names = names(load_json("catalog.json", {"menus": []}))
    show = visible(products)
    ids = {p["id"] for p in show}
    for p in products:
        p["page"] = p["id"] in ids
    from dz_common import save_json
    data = load_json("products.json", {})
    data["products"] = products
    save_json("products.json", data)
    idx = load_json("index.json", {"products": []})
    for x in idx.get("products", []):
        x["page"] = x.get("id") in ids
    save_json("index.json", idx)

    pages_dir = os.path.join(ROOT, "p")
    shutil.rmtree(pages_dir, ignore_errors=True)  # se regeneran todas: así no quedan páginas de piezas retiradas
    os.makedirs(pages_dir, exist_ok=True)
    with open(os.path.join(pages_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write('<!DOCTYPE html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url=../"><title>DAZMOND &amp; Co.</title>')
    for p in show:
        d = os.path.join(pages_dir, p["id"])
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as f:
            f.write(product_page(p, site, cat_names))

    os.makedirs(os.path.join(ROOT, "feeds"), exist_ok=True)
    rate = cfg.get("fx", {}).get("EUR", 0.92)
    for lang in ("es", "en"):
        with open(os.path.join(ROOT, "feeds", f"catalogo-{lang}.xml"), "w", encoding="utf-8") as f:
            f.write(feed(lang, show, site, cat_names, rate))

    urls = [f"{site}/"] + [f"{site}/p/{p['id']}/" for p in show]
    with open(os.path.join(ROOT, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                + "\n".join(f"<url><loc>{u}</loc><lastmod>{TODAY}</lastmod></url>" for u in urls) + "\n</urlset>\n")
    with open(os.path.join(ROOT, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(f"User-agent: *\nAllow: /\nDisallow: /scripts/\nDisallow: /data/suppliers/\nSitemap: {site}/sitemap.xml\n")
    print(f"[escaparates] {len(show)} páginas de producto y 2 catálogos listos ({site}/feeds/catalogo-es.xml)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
