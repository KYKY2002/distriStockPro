# =============================================================================
# DistriStock Pro — Microservice VENTES (Flask)
# Rôle : vitrine boutique, panier (session), commandes en base.
# Achat : appel HTTP vers le service Inventaire (DNS Docker : inventory-app:5001).
# =============================================================================
import os
import secrets
import string
from contextlib import contextmanager
from decimal import Decimal

import psycopg2
import requests
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

app = Flask(__name__)
# Clé secrète pour signer le cookie de session (obligatoire en production)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-change-in-production")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
# URL interne du microservice Inventaire (résolution DNS par le réseau Docker)
INVENTORY_SERVICE_URL = os.environ.get(
    "INVENTORY_SERVICE_URL", "http://inventory-app:5001"
).rstrip("/")

# Libellés affichés pour les moyens de paiement (valeurs stockées = clés API)
PAYMENT_LABELS = {
    "orange_money": "Orange Money",
    "wave": "Wave",
    "card": "Carte bancaire",
    "cash": "Espèces",
}


@contextmanager
def get_db():
    """Connexion PostgreSQL avec commit automatique si aucune exception."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def generate_order_reference():
    """Référence unique lisible pour la démo (ex. ORD-A1B2C3D4)."""
    alphabet = string.ascii_uppercase + string.digits
    suffix = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"ORD-{suffix}"


def get_cart():
    """Panier stocké en session : { sku: quantité (int) }."""
    raw = session.get("cart") or {}
    out = {}
    for sku, q in raw.items():
        try:
            qi = int(q)
        except (TypeError, ValueError):
            continue
        if qi > 0:
            out[str(sku)] = qi
    return out


def load_active_products():
    """Charge le catalogue depuis la table products (lecture seule, même base que l’inventaire)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT sku, name, description, category, sale_price, quantity_stock,
                       low_stock_threshold, image_url
                FROM products
                WHERE status = 'active'
                ORDER BY name
                """
            )
            rows = cur.fetchall()
    products = []
    for r in rows:
        sale_price = r[4]
        if isinstance(sale_price, Decimal):
            sale_price = float(sale_price)
        products.append(
            {
                "sku": r[0],
                "name": r[1],
                "description": r[2] or "",
                "category": r[3] or "",
                "sale_price": sale_price,
                "quantity_stock": r[5],
                "low_stock_threshold": r[6],
                "image_url": r[7] or "",
            }
        )
    return products


def cart_details():
    """
    Recalcule lignes panier + total à partir des prix courants en base.
    Retourne (lignes, total_decimal) ou (None, None) si incohérence (SKU invalide).
    """
    cart = get_cart()
    if not cart:
        return [], Decimal("0")

    with get_db() as conn:
        with conn.cursor() as cur:
            lines = []
            total = Decimal("0")
            for sku, qty in cart.items():
                cur.execute(
                    """
                    SELECT name, sale_price, quantity_stock
                    FROM products
                    WHERE sku = %s AND status = 'active'
                    """,
                    (sku,),
                )
                row = cur.fetchone()
                if not row:
                    return None, None
                name, sale_price, stock = row
                if qty > stock:
                    return None, None
                line_total = Decimal(str(sale_price)) * qty
                total += line_total
                lines.append(
                    {
                        "sku": sku,
                        "name": name,
                        "quantity": qty,
                        "unit_price": float(sale_price),
                        "line_total": float(line_total),
                    }
                )
    return lines, total


@app.route("/health")
def health():
    return jsonify({"service": "sales-app", "status": "ok"}), 200


@app.route("/")
def shop():
    """Page boutique : grille produits, design Tailwind + effets hover."""
    products = load_active_products()
    return render_template("shop.html", products=products, payment_labels=PAYMENT_LABELS)


@app.route("/cart/add", methods=["POST"])
def cart_add():
    """Ajoute au panier (quantité bornée par le stock disponible)."""
    sku = (request.form.get("sku") or "").strip()
    try:
        qty = int(request.form.get("quantity") or 0)
    except ValueError:
        flash("Quantité invalide.", "error")
        return redirect(url_for("shop"))

    if not sku or qty < 1:
        flash("Sélectionnez un produit et une quantité.", "error")
        return redirect(url_for("shop"))

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT quantity_stock FROM products WHERE sku = %s AND status = 'active'",
                (sku,),
            )
            row = cur.fetchone()
    if not row:
        flash("Produit introuvable.", "error")
        return redirect(url_for("shop"))
    (stock,) = row

    cart = get_cart()
    already = cart.get(sku, 0)
    if already + qty > stock:
        flash(f"Stock insuffisant pour {sku} (disponible : {stock}).", "error")
        return redirect(url_for("shop"))

    cart[sku] = already + qty
    session["cart"] = cart
    session.modified = True
    flash("Produit ajouté au panier.", "success")
    return redirect(url_for("shop"))


@app.route("/cart")
def cart_page():
    """Récapitulatif du panier."""
    lines, total = cart_details()
    if lines is None:
        session.pop("cart", None)
        flash("Panier invalide (stock ou produit). Panier réinitialisé.", "error")
        return redirect(url_for("shop"))
    return render_template(
        "cart.html",
        lines=lines,
        total=float(total),
        payment_labels=PAYMENT_LABELS,
    )


@app.route("/cart/remove/<sku>", methods=["POST"])
def cart_remove(sku):
    cart = get_cart()
    cart.pop(sku, None)
    session["cart"] = cart
    session.modified = True
    flash("Ligne retirée.", "success")
    return redirect(url_for("cart_page"))


@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    """
    GET : formulaire client + moyen de paiement + barre de progression.
    POST : appelle l’API inventaire puis enregistre la commande (table orders).
    """
    lines, total = cart_details()
    if lines is None:
        session.pop("cart", None)
        flash("Panier invalide.", "error")
        return redirect(url_for("shop"))

    if not lines:
        flash("Votre panier est vide.", "error")
        return redirect(url_for("shop"))

    if request.method == "GET":
        return render_template(
            "checkout.html",
            lines=lines,
            total=float(total),
            payment_labels=PAYMENT_LABELS,
            payment_keys=list(PAYMENT_LABELS.keys()),
        )

    customer_name = (request.form.get("customer_name") or "").strip()
    email = (request.form.get("email") or "").strip()
    payment_method = (request.form.get("payment_method") or "").strip()

    if not customer_name or not email:
        flash("Nom et e-mail sont obligatoires.", "error")
        return redirect(url_for("checkout"))

    if payment_method not in PAYMENT_LABELS:
        flash("Moyen de paiement invalide.", "error")
        return redirect(url_for("checkout"))

    # Revalidation panier / total avant paiement
    lines, total = cart_details()
    if lines is None or not lines:
        flash("Panier expiré ou invalide.", "error")
        return redirect(url_for("shop"))

    payload = {"items": [{"sku": ln["sku"], "quantity": ln["quantity"]} for ln in lines]}

    try:
        # Communication East-West : HTTP vers le conteneur inventory-app (nom de service Docker)
        resp = requests.post(
            f"{INVENTORY_SERVICE_URL}/api/check-and-reduce",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
    except requests.exceptions.ConnectionError:
        app.logger.warning("Inventaire injoignable (réseau ou conteneur arrêté)")
        flash(
            "Service inventaire temporairement indisponible. Réessayez plus tard.",
            "error",
        )
        return redirect(url_for("checkout"))
    except requests.exceptions.Timeout:
        flash("Délai dépassé lors de la réservation du stock.", "error")
        return redirect(url_for("checkout"))
    except requests.exceptions.RequestException as e:
        app.logger.exception("Erreur HTTP vers inventaire: %s", e)
        flash("Erreur de communication avec le service inventaire.", "error")
        return redirect(url_for("checkout"))

    if resp.status_code != 200:
        try:
            data = resp.json()
            msg = data.get("error", "Stock insuffisant ou erreur inventaire")
        except ValueError:
            msg = "Erreur inventaire (réponse non JSON)"
        flash(msg, "error")
        return redirect(url_for("checkout"))

    order_ref = generate_order_reference()
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO orders (order_reference, customer_name, email, total_amount,
                                        payment_method, order_status)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        order_ref,
                        customer_name,
                        email,
                        total,
                        payment_method,
                        "validated",
                    ),
                )
    except psycopg2.Error:
        app.logger.exception("Échec insertion commande après déstockage — intervention manuelle requise")
        flash(
            "Commande enregistrée côté stock mais erreur base commandes. Contactez le support.",
            "error",
        )
        return redirect(url_for("checkout"))

    session.pop("cart", None)
    session.modified = True
    return redirect(url_for("order_success", ref=order_ref))


@app.route("/order/<ref>")
def order_success(ref):
    """Page de confirmation avec barre de progression « terminée »."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT order_reference, customer_name, email, total_amount, payment_method, order_status, created_at
                FROM orders
                WHERE order_reference = %s
                """,
                (ref,),
            )
            row = cur.fetchone()
    if not row:
        flash("Commande introuvable.", "error")
        return redirect(url_for("shop"))
    order = {
        "order_reference": row[0],
        "customer_name": row[1],
        "email": row[2],
        "total_amount": float(row[3]) if row[3] is not None else 0,
        "payment_method": PAYMENT_LABELS.get(row[4], row[4]),
        "order_status": row[5],
        "created_at": row[6].isoformat() if row[6] else "",
    }
    return render_template("order_success.html", order=order)


if __name__ == "__main__":
    port = int(os.environ.get("SALES_PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
