#!/usr/bin/env python3
"""
DAZMOND & Co. — Informe diario para ti por Telegram (solo Python estándar).

Cada mañana te llega:
  - ventas e ingresos de ayer y de los últimos 30 días (Stripe),
  - las piezas que más se venden y las más vistas,
  - visitas a la web (Firebase, si está conectado),
  - suscriptores del boletín (Brevo),
  - campaña activa y ofertas,
  - avisos: productos sin foto, categorías por revisar.
"""

import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dz_common import NOW, brevo, env, load_json, notify, stripe, tg_ready  # noqa: E402


GOAL_USD = 144_000_000_000


def lifetime_revenue():
    """Todo lo cobrado desde siempre en Stripe (las 3 tiendas comparten la cuenta), sin devoluciones.
    Solo se calcula y se envía por Telegram: no se guarda en el repositorio público."""
    total, after, pages = 0.0, None, 0
    while pages < 100:
        q = "/charges?limit=100" + (f"&starting_after={after}" if after else "")
        data = stripe(q)
        for c in data.get("data", []):
            if c.get("paid") and c.get("status") == "succeeded":
                total += ((c.get("amount_captured") or c.get("amount") or 0) - (c.get("amount_refunded") or 0)) / 100
        if not data.get("has_more") or not data.get("data"):
            break
        after = data["data"][-1]["id"]
        pages += 1
    return total


def goal_line():
    if not env("STRIPE_SECRET_KEY"):
        return None
    try:
        got = lifetime_revenue()
    except Exception as e:
        return f"🏁 Marcador: no disponible ({str(e)[:50]})"
    pct = got / GOAL_USD * 100
    filled = min(20, int(pct / 5))
    bar = "█" * filled + "░" * (20 - filled)
    return (f"🏁 MARCADOR GRUPO DAZ\n   Ganado: {got:,.2f} $ de 144.000.000.000 $\n   {bar} {pct:.8f} %\n"
            f"   Faltan: {GOAL_USD - got:,.0f} $")


def main():
    if not tg_ready():
        print("[informe] Sin Telegram: no se envía informe.")
        return 0
    cfg = load_json("config.json", {})
    prods = load_json("products.json", {"products": []}).get("products", [])
    stats = load_json("store_analytics.json", {"products": {}}).get("products", {})
    camp = load_json("campaign.json", {"active": False})
    by_id = {p["id"]: p for p in prods}
    lines = [f"✦ DAZMOND & Co. · Informe del {NOW.strftime('%d/%m/%Y')}", ""]
    g = goal_line()
    if g:
        lines += [g, ""]

    if env("STRIPE_SECRET_KEY"):
        try:
            y0 = int((NOW - timedelta(days=1)).timestamp())
            n1 = r1 = 0
            data = stripe(f"/checkout/sessions?limit=100&status=complete&created[gte]={y0}")
            for s in data.get("data", []):
                if s.get("payment_status") == "paid":
                    n1 += 1
                    r1 += (s.get("amount_total") or 0) / 100
            lines.append(f"💰 Últimas 24 h: {n1} ventas · {r1:,.2f} $")
        except Exception as e:
            lines.append(f"💰 Ventas: no disponibles ({str(e)[:60]})")
    else:
        lines.append("💰 Ventas: falta conectar Stripe")

    s30 = sum(v.get("sales_30d", 0) for v in stats.values())
    rev30 = sum(v.get("revenue_30d", 0) for v in stats.values())
    lines.append(f"📈 Últimos 30 días: {s30} ventas · {rev30:,.2f} $")
    views = sum(v.get("views", 0) for v in stats.values())
    today = sum(v.get("views_today", 0) for v in stats.values())
    lines.append(f"👁️ Visitas a fichas (30 días): {views} · hoy: {today}" if views else "👁️ Visitas: conecta Firebase para verlas")

    top = sorted(stats.items(), key=lambda kv: -kv[1].get("sales_30d", 0))[:3]
    top = [(k, v) for k, v in top if v.get("sales_30d")]
    if top:
        lines.append("\n🏆 Lo más vendido:")
        lines += [f"  · {by_id.get(k, {}).get('name_es', k)} — {v['sales_30d']}" for k, v in top]
    seen = sorted(stats.items(), key=lambda kv: -kv[1].get("views", 0))[:3]
    seen = [(k, v) for k, v in seen if v.get("views")]
    if seen:
        lines.append("\n🔥 Lo más visto:")
        lines += [f"  · {by_id.get(k, {}).get('name_es', k)} — {v['views']} visitas" for k, v in seen]

    nl = cfg.get("newsletter", {})
    if env("BREVO_API_KEY"):
        for lang in ("es", "en"):
            lid = int(nl.get(f"brevo_list_id_{lang}") or 0)
            if lid:
                try:
                    info = brevo(f"/contacts/lists/{lid}")
                    lines.append(f"✉️ Suscriptores {lang.upper()}: {info.get('uniqueSubscribers', info.get('totalSubscribers', '?'))}")
                except Exception:
                    pass

    lines.append(f"\n🎯 Campaña: {camp['es']['title']} · {len(camp.get('offers', []))} ofertas"
                 if camp.get("active") else "\n🎯 Sin campaña activa")
    lines.append(f"🧵 Catálogo: {len(prods)} piezas · {sum(1 for p in prods if p.get('available'))} a la venta"
                 f" · {sum(1 for p in prods if p.get('archived'))} archivadas"
                 f" · {sum(1 for p in prods if not p.get('ai_done') and not p.get('archived'))} pendientes de revisar por la IA")
    no_photo = [p for p in prods if not p.get("display_images")]
    review = [p for p in prods if p.get("needs_review")]
    if no_photo:
        lines.append(f"📷 {len(no_photo)} piezas sin foto nítida (no salen en redes).")
    if review:
        lines.append(f"🔎 {len(review)} piezas con la sección por revisar: " + ", ".join(p.get("name_es", p["id"]) for p in review[:5]))
    q = load_json("quality_log.json", {"days": []}).get("days", [])
    if q:
        last = q[-1]
        lines.append(f"\n🛡️ Vigilante de calidad ({last['date']}): {len(last.get('fixes', []))} correcciones hechas solas")
        lines += [f"  · {f}" for f in last.get("fixes", [])[:8]]
    notify("\n".join(lines))
    print("[informe] Enviado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
