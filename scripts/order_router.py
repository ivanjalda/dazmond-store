#!/usr/bin/env python3
"""
DAZMOND & Co. — Pedidos automáticos (solo Python estándar). Se ejecuta cada hora.

  1. Mira en Stripe las compras pagadas de los últimos 3 días.
  2. Las que aún no se han enviado al taller:
       - Printify  → crea el pedido y lo manda a producción, solo.
       - Printful  → crea el pedido y lo confirma, solo.
       - POPCUSTOMS, Inkpod, InterestPrint y otros → te manda a Telegram todos los datos
         para que lo pidas tú en su web en 1 minuto.
  3. Marca la compra en Stripe como "enviada" para no repetirla nunca.
  4. Te avisa de cada venta por Telegram.

PRIVACIDAD: el repositorio es público. Nada de nombres, correos ni direcciones se escribe en
los registros ni en archivos: solo viajan de Stripe al taller y a tu Telegram privado.
"""

import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dz_common import NOW, env, load_json, notify, printful, printify, printify_shop_id, stripe  # noqa: E402

FLAG = "dazmond_enviado"


def log(msg):
    print(f"[pedidos] {msg}", flush=True)


def shipping_of(s):
    sd = s.get("shipping_details") or (s.get("collected_information") or {}).get("shipping_details") or {}
    cd = s.get("customer_details") or {}
    addr = sd.get("address") or cd.get("address") or {}
    name = sd.get("name") or cd.get("name") or ""
    first, _, last = name.partition(" ")
    return {"name": name, "first": first or name, "last": last or "-", "email": cd.get("email") or "",
            "phone": cd.get("phone") or "", "line1": addr.get("line1") or "", "line2": addr.get("line2") or "",
            "city": addr.get("city") or "", "state": addr.get("state") or "", "zip": addr.get("postal_code") or "",
            "country": addr.get("country") or ""}


def size_of(s, p):
    for f in s.get("custom_fields") or []:
        if f.get("key") == "talla":
            val = (f.get("dropdown") or {}).get("value") or (f.get("text") or {}).get("value") or ""
            return (p.get("size_values") or {}).get(val, val)
    return None


def variant_for(p, size):
    vm = p.get("variant_map") or {}
    if size and size in vm:
        return vm[size]
    if len(vm) == 1:
        return next(iter(vm.values()))
    return None


def send_printify(p, vid, qty, a, ref, cfg):
    shop = printify_shop_id()
    order = printify(f"/shops/{shop}/orders.json", {
        "external_id": ref, "label": ref[-12:],
        "line_items": [{"product_id": p.get("supplier_product_id"), "variant_id": int(vid), "quantity": qty}],
        "shipping_method": 1, "send_shipping_notification": False,
        "address_to": {"first_name": a["first"], "last_name": a["last"], "email": a["email"], "phone": a["phone"],
                       "country": a["country"], "region": a["state"], "address1": a["line1"],
                       "address2": a["line2"], "city": a["city"], "zip": a["zip"]},
    })
    oid = order.get("id")
    if cfg.get("orders", {}).get("auto_send_to_production", True) and oid:
        printify(f"/shops/{shop}/orders/{oid}/send_to_production.json", {})
    return f"printify:{oid}"


def send_printful(p, vid, qty, a, ref, cfg):
    confirm = "true" if cfg.get("orders", {}).get("auto_send_to_production", True) else "false"
    order = printful(f"/orders?confirm={confirm}", {
        "external_id": ref[-32:],
        "recipient": {"name": a["name"], "address1": a["line1"], "address2": a["line2"], "city": a["city"],
                      "state_code": a["state"], "country_code": a["country"], "zip": a["zip"],
                      "email": a["email"], "phone": a["phone"]},
        "items": [{"sync_variant_id": int(vid), "quantity": qty}],
    })
    return f"printful:{(order.get('result') or {}).get('id')}"


def main():
    if not env("STRIPE_SECRET_KEY"):
        log("Sin STRIPE_SECRET_KEY: todavía no hay pedidos que revisar.")
        return 0
    cfg = load_json("config.json", {})
    products = {p["id"]: p for p in load_json("products.json", {"products": []}).get("products", [])}
    since = int((NOW - timedelta(days=3)).timestamp())
    sessions, after = [], None
    for _ in range(10):
        q = f"/checkout/sessions?limit=100&status=complete&created[gte]={since}"
        data = stripe(q + (f"&starting_after={after}" if after else ""))
        sessions += data.get("data", [])
        if not data.get("has_more") or not data.get("data"):
            break
        after = data["data"][-1]["id"]
    done = errors = 0
    for s in sessions:
        if s.get("payment_status") != "paid" or not s.get("payment_intent"):
            continue
        pi_id = s["payment_intent"] if isinstance(s["payment_intent"], str) else s["payment_intent"]["id"]
        pi = stripe(f"/payment_intents/{pi_id}")
        if (pi.get("metadata") or {}).get(FLAG):
            continue
        full = stripe(f"/checkout/sessions/{s['id']}?expand[]=line_items")
        pid = (full.get("metadata") or {}).get("dazmond_id")
        p = products.get(pid)
        qty = sum(li.get("quantity", 1) for li in (full.get("line_items") or {}).get("data", [])) or 1
        a = shipping_of(full)
        size = size_of(full, p or {})
        amount = (full.get("amount_total") or 0) / 100
        ref = s["id"]
        name = (p or {}).get("name_es", pid or "¿?")
        head = f"🛍️ NUEVA VENTA · {amount:.2f} {str(full.get('currency', 'usd')).upper()}\n{name} · Talla: {size or '-'} · Cantidad: {qty}"
        try:
            if not p:
                raise RuntimeError("no encuentro el producto en el catálogo")
            vid = variant_for(p, size)
            supplier = (p.get("supplier") or "").lower()
            if supplier == "printify" and env("PRINTIFY_TOKEN") and vid:
                result = send_printify(p, vid, qty, a, ref, cfg)
                notify(f"{head}\n✓ Pedido enviado solo a Printify ({result}).")
            elif supplier == "printful" and env("PRINTFUL_TOKEN") and vid:
                result = send_printful(p, vid, qty, a, ref, cfg)
                notify(f"{head}\n✓ Pedido enviado solo a Printful ({result}).")
            else:
                result = "manual"
                notify(f"{head}\n\n👉 Haz este pedido tú en {p.get('supplier')}:\n"
                       f"{p.get('source_title', '')}\nTalla: {size or '-'} · Cantidad: {qty}"
                       f"{(' · SKU: ' + str((p.get('sku_map') or {}).get(size or 'unica', ''))) if (p.get('sku_map') or {}).get(size or 'unica') else ''}\n\n"
                       f"Enviar a:\n{a['name']}\n{a['line1']} {a['line2']}\n{a['zip']} {a['city']} {a['state']}\n"
                       f"{a['country']}\nTel: {a['phone'] or '-'} · {a['email']}\n\nReferencia: {ref[-12:]}")
            stripe(f"/payment_intents/{pi_id}", {f"metadata[{FLAG}]": result})
            done += 1
        except Exception as e:
            errors += 1
            log(f"Un pedido no se pudo enviar: {str(e)[:200]}")
            notify(f"{head}\n⚠️ No pude enviarlo solo: {str(e)[:250]}\nRevísalo en Stripe (referencia {ref[-12:]}).")
            try:  # se marca para no repetir el aviso cada hora; lo revisas tú
                stripe(f"/payment_intents/{pi_id}", {f"metadata[{FLAG}]": "error-revisar"})
            except Exception:
                pass
    log(f"Pedidos nuevos gestionados: {done} · con incidencia: {errors}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
