#!/usr/bin/env python3
"""
DAZMOND — Mudanza de Printify: copia TODOS los productos de la tienda antigua (unida a Shopify)
a la tienda nueva "DAZMOND Web" (por API, sin Shopify). Así se puede cerrar Shopify sin perder nada.

- Va por tandas (Printify limita cuántos productos se crean seguidos) y sigue en la siguiente
  ejecución donde lo dejó. Guarda el progreso en data/printify_migration.json.
- Cuando termina, marca la tienda nueva como la activa: desde ese momento el motor, el estudio
  de diseño y los pedidos usan la tienda nueva. Te avisa por Telegram: "Ya puedes cerrar Shopify
  (parte Printify)".
- No borra nada de la tienda antigua.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dz_common import env, load_json, notify, printify, save_json  # noqa: E402

TARGET = "29895090"            # tienda nueva "DAZMOND Web" (API)
PER_RUN = 120                  # productos por ejecución (Printify limita la creación)
TIME_BUDGET = 8 * 60


def log(m):
    print(f"[mudanza-printify] {m}", flush=True)


def main():
    if not env("PRINTIFY_TOKEN"):
        return 0
    st = load_json("printify_migration.json", {"source": None, "map": {}, "failed": {}, "page": 1, "done": False})
    if st.get("done"):
        log("La mudanza ya terminó.")
        return 0
    source = st.get("source") or env("PRINTIFY_SHOP_ID")
    if not source:
        shops = printify("/shops.json") or []
        old = next((s for s in shops if str(s["id"]) != TARGET and "daz" in s.get("title", "").lower()
                    and "premium" not in s.get("title", "").lower()), None)
        source = str(old["id"]) if old else None
    if not source or str(source) == TARGET:
        log("No encuentro la tienda antigua.")
        return 0
    st["source"] = str(source)
    start, made = time.time(), 0
    page = st.get("page", 1)
    while made < PER_RUN and time.time() - start < TIME_BUDGET:
        data = printify(f"/shops/{source}/products.json?limit=50&page={page}")
        items = data.get("data", [])
        if not items:
            st["done"] = True
            break
        for p in items:
            pid = p["id"]
            if pid in st["map"] or st["failed"].get(pid, 0) >= 2:
                continue
            if made >= PER_RUN or time.time() - start > TIME_BUDGET:
                break
            try:
                body = {
                    "title": p.get("title", ""),
                    "description": p.get("description", ""),
                    "tags": p.get("tags", []),
                    "blueprint_id": p["blueprint_id"],
                    "print_provider_id": p["print_provider_id"],
                    "variants": [{"id": v["id"], "price": v.get("price", 4999), "is_enabled": v.get("is_enabled", True)}
                                 for v in p.get("variants", []) if v.get("is_enabled")] or
                                [{"id": v["id"], "price": v.get("price", 4999), "is_enabled": True}
                                 for v in p.get("variants", [])[:50]],
                    "print_areas": [{"variant_ids": a.get("variant_ids", []),
                                     "placeholders": [{"position": ph.get("position"),
                                                       "images": [{k: im[k] for k in ("id", "x", "y", "scale", "angle")
                                                                   if k in im} for im in ph.get("images", [])]}
                                                      for ph in a.get("placeholders", [])]}
                                    for a in p.get("print_areas", [])],
                }
                enabled = {v["id"] for v in body["variants"]}
                for a in body["print_areas"]:
                    a["variant_ids"] = [v for v in a["variant_ids"] if v in enabled]
                body["print_areas"] = [a for a in body["print_areas"] if a["variant_ids"]]
                new = printify(f"/shops/{TARGET}/products.json", body)
                st["map"][pid] = new.get("id")
                made += 1
                time.sleep(1.2)
            except Exception as e:
                st["failed"][pid] = st["failed"].get(pid, 0) + 1
                log(f"No pude copiar «{p.get('title', pid)[:50]}»: {str(e)[:150]}")
        if data.get("current_page", page) >= data.get("last_page", page) and all(
                (x["id"] in st["map"] or st["failed"].get(x["id"], 0) >= 2) for x in items):
            st["done"] = True
            break
        if all((x["id"] in st["map"] or st["failed"].get(x["id"], 0) >= 2) for x in items):
            page += 1
            st["page"] = page
        else:
            break
    log(f"Copiados en esta tanda: {made} · total copiados: {len(st['map'])} · fallidos: {len(st['failed'])}")
    if st.get("done"):
        st["active_shop"] = TARGET
        notify(f"✅ Printify mudado: {len(st['map'])} productos copiados a la tienda nueva «DAZMOND Web» "
               f"(sin Shopify). {len(st['failed'])} no se pudieron copiar. La parte de Printify ya no depende de Shopify.")
    elif made:
        notify(f"🚚 Mudanza de Printify: {len(st['map'])} productos copiados a la tienda nueva. Sigue en la próxima hora.")
    save_json("printify_migration.json", st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
