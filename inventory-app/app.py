# =============================================================================
# DistriStock Pro — Microservice INVENTAIRE (Flask)
# Rôle : catalogue, stocks, alertes basse quantité, API pour le service Ventes.
# Communication East-West : exposé sur le réseau Docker sous le nom "inventory-app".
# =============================================================================
import os
from collections import defaultdict
from contextlib import contextmanager
from decimal import Decimal

import psycopg2
from flask import Flask, jsonify, render_template, request

# ---------------------------------------------------------------------------
# Application Flask : point d'entrée WSGI (gunicorn charge "app")
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

# Chaîne de connexion injectée par docker-compose (jamais de mot de passe en dur)
DATABASE_URL = os.environ.get("DATABASE_URL", "")


@contextmanager
def get_db():
    """
    Contexte de connexion PostgreSQL.
    Utilisé avec 'with get_db() as conn:' pour garantir commit/rollback et fermeture.
    """
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row_to_product(row):
    """Convertit une ligne SQL en dictionnaire JSON-friendly (Decimal -> str)."""
    if not row:
        return None
    keys = [
        "id",
        "uuid",
        "sku",
        "name",
        "description",
        "category",
        "purchase_price",
        "sale_price",
        "quantity_stock",
        "low_stock_threshold",
        "image_url",
        "status",
        "created_at",
        "updated_at",
    ]
    d = dict(zip(keys, row))
    for k in ("purchase_price", "sale_price"):
        if d[k] is not None and isinstance(d[k], Decimal):
            d[k] = str(d[k])
    if d.get("uuid"):
        d["uuid"] = str(d["uuid"])
    if d.get("created_at"):
        d["created_at"] = d["created_at"].isoformat()
    if d.get("updated_at"):
        d["updated_at"] = d["updated_at"].isoformat()
    return d


@app.route("/health")
def health():
    """Sonde simple pour vérifier que le processus répond (utile pour debug / démo)."""
    return jsonify({"service": "inventory-app", "status": "ok"}), 200


@app.route("/")
def dashboard():
    """
    Interface web de suivi des stocks (vue « back-office »).
    Affiche les alertes lorsque quantity_stock <= low_stock_threshold.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, uuid, sku, name, description, category,
                       purchase_price, sale_price, quantity_stock, low_stock_threshold,
                       image_url, status, created_at, updated_at
                FROM products
                ORDER BY category, name
                """
            )
            rows = cur.fetchall()
    products = [row_to_product(r) for r in rows]
    return render_template("dashboard.html", products=products)


@app.route("/api/products", methods=["GET"])
def api_products():
    """Liste JSON de tous les produits actifs (pour intégrations / tests)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, uuid, sku, name, description, category,
                       purchase_price, sale_price, quantity_stock, low_stock_threshold,
                       image_url, status, created_at, updated_at
                FROM products
                WHERE status = 'active'
                ORDER BY name
                """
            )
            rows = cur.fetchall()
    return jsonify({"products": [row_to_product(r) for r in rows]})


@app.route("/api/check-and-reduce", methods=["POST"])
def api_check_and_reduce():
    """
    Point d'appel inter-services (Ventes -> Inventaire).

    Corps JSON attendu :
      - soit { "sku": "...", "quantity": n }
      - soit { "items": [ {"sku": "...", "quantity": n}, ... ] }

    Transaction : verrouillage des lignes (FOR UPDATE), vérification du stock,
    puis décrément atomique. Tout échec -> rollback et HTTP 400.
    """
    payload = request.get_json(silent=True) or {}
    items = []

    if "items" in payload and isinstance(payload["items"], list):
        for it in payload["items"]:
            sku = (it or {}).get("sku")
            qty = (it or {}).get("quantity")
            if not sku or qty is None:
                return jsonify({"error": "Chaque item doit avoir sku et quantity"}), 400
            try:
                q = int(qty)
            except (TypeError, ValueError):
                return jsonify({"error": "Quantité invalide"}), 400
            if q < 1:
                return jsonify({"error": "Quantité doit être >= 1"}), 400
            items.append((str(sku).strip(), q))
    else:
        sku = payload.get("sku")
        qty = payload.get("quantity")
        if not sku or qty is None:
            return jsonify({"error": "Fournir sku et quantity, ou items[]"}), 400
        try:
            q = int(qty)
        except (TypeError, ValueError):
            return jsonify({"error": "Quantité invalide"}), 400
        if q < 1:
            return jsonify({"error": "Quantité doit être >= 1"}), 400
        items = [(str(sku).strip(), q)]

    if not items:
        return jsonify({"error": "Panier vide"}), 400

    # Fusion des lignes identiques (même SKU) pour un seul verrouillage / UPDATE cohérent
    qty_by_sku = defaultdict(int)
    for sku, q in items:
        qty_by_sku[sku] += q
    merged_items = sorted(qty_by_sku.items(), key=lambda x: x[0])

    # Connexion explicite : on commit uniquement si tout le flux réussit (pas de commit sur erreur 4xx)
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            # Verrouillage dans l'ordre des SKU (évite les interblocages entre transactions)
            for sku, _need in merged_items:
                cur.execute(
                    """
                    SELECT id, quantity_stock, status
                    FROM products
                    WHERE sku = %s
                    FOR UPDATE
                    """,
                    (sku,),
                )
                row = cur.fetchone()
                if not row:
                    conn.rollback()
                    return jsonify({"error": f"Produit inconnu: {sku}"}), 400
                _pid, stock, status = row
                if status != "active":
                    conn.rollback()
                    return jsonify({"error": f"Produit inactif: {sku}"}), 400

            remaining = {}
            for sku, need in merged_items:
                cur.execute(
                    "SELECT quantity_stock FROM products WHERE sku = %s FOR UPDATE",
                    (sku,),
                )
                (stock,) = cur.fetchone()
                if stock < need:
                    conn.rollback()
                    return (
                        jsonify(
                            {
                                "error": "Stock insuffisant",
                                "sku": sku,
                                "available": stock,
                                "requested": need,
                            }
                        ),
                        400,
                    )
                remaining[sku] = stock - need

            for sku, need in merged_items:
                cur.execute(
                    """
                    UPDATE products
                    SET quantity_stock = quantity_stock - %s,
                        updated_at = NOW()
                    WHERE sku = %s
                    """,
                    (need, sku),
                )

            out = [{"sku": s, "remaining": remaining[s]} for s, _ in merged_items]
        conn.commit()
        return jsonify({"ok": True, "details": out}), 200
    except psycopg2.Error:
        conn.rollback()
        app.logger.exception("Erreur PostgreSQL dans check-and-reduce")
        return jsonify({"error": "Erreur base de données inventaire"}), 500
    finally:
        conn.close()


if __name__ == "__main__":
    # Mode développement local uniquement
    port = int(os.environ.get("INVENTORY_PORT", "5001"))
    app.run(host="0.0.0.0", port=port, debug=True)
