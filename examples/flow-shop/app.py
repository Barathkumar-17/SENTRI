"""flow-shop: a small multi-step shop used to validate Sentri's adaptive exploration.

Sign up, log in, browse, add to cart, give an address, pay (with an optional coupon), confirm, see your orders.
State is in memory and re-created on every start, so a restart is a clean state. Some functional failures are
built in; which ones, and how to trigger them, is recorded only in the validation harness
(tests/validation/flow_shop_expected.yaml), never in anything Sentri is given.
"""

from __future__ import annotations

import os
import secrets
import socket

from flask import Flask, abort, redirect, render_template_string, request, session, url_for
from markupsafe import escape

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

USERS: dict[str, dict] = {}
PRODUCTS = [
    {"id": 1, "name": "Blue mug", "price": 12.0},
    {"id": 2, "name": "Desk lamp", "price": 35.0},
    {"id": 3, "name": "Notebook", "price": 4.5},
]
CARTS: dict[str, dict[int, int]] = {}
ORDERS: list[dict] = []
PENDING: dict[str, dict] = {}

BASE = """<!doctype html><html><head><meta charset="utf-8"><title>{{ title }}</title></head><body>
<nav><a href="/">Shop</a> <a href="/cart">Cart</a> <a href="/orders">Orders</a>
{% if user %}<span>{{ user }}</span>{% else %}<a href="/login">Log in</a> <a href="/signup">Sign up</a>{% endif %}</nav>
{{ body|safe }}</body></html>"""


def page(title: str, body: str):
    return render_template_string(BASE, title=title, body=body, user=session.get("user"))


def need_user() -> str:
    user = session.get("user")
    if user is None:
        abort(redirect(url_for("login")))
    return user


@app.get("/")
def shop():
    rows = "".join(
        f'<li class="product-row"><a href="/product/{p["id"]}">{p["name"]}</a> ${p["price"]}</li>' for p in PRODUCTS
    )
    return page("Shop", f"<h1>Shop</h1><ul>{rows}</ul>")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        if not email or not request.form.get("password"):
            return page("Sign up", "<p>Email and password are required</p>"), 400
        USERS[email] = {"password": request.form["password"]}
        return redirect(url_for("login"))
    return page(
        "Sign up",
        '<h1>Sign up</h1><form method="post"><input name="email" type="email">'
        '<input name="password" type="password"><button id="signup-btn">Create account</button></form>',
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "")
        if USERS.get(email, {}).get("password") != request.form.get("password"):
            return page("Log in", "<p>Wrong email or password</p>"), 401
        session["user"] = email
        return redirect(url_for("shop"))
    return page(
        "Log in",
        '<h1>Log in</h1><form method="post"><input name="email" type="email">'
        '<input name="password" type="password"><button id="login-btn">Log in</button></form>',
    )


@app.get("/product/<int:pid>")
def product(pid: int):
    p = next((p for p in PRODUCTS if p["id"] == pid), None)
    if p is None:
        abort(404)
    return page(
        p["name"],
        f'<h1>{p["name"]}</h1><p>${p["price"]}</p><form method="post" action="/cart/add">'
        f'<input type="hidden" name="pid" value="{pid}"><label>Quantity <input name="qty" type="number" value="1">'
        '</label><button id="add-btn">Add to cart</button></form>',
    )


@app.post("/cart/add")
def cart_add():
    user = need_user()
    try:
        pid, qty = int(request.form.get("pid", "0")), int(request.form.get("qty", "1"))
    except ValueError:
        return page("Cart", "<p>Quantity must be a number</p>"), 400
    if qty < 0:
        return page("Cart", "<p>Quantity cannot be negative</p>"), 400
    cart = CARTS.setdefault(user, {})
    cart[pid] = cart.get(pid, 0) + qty
    return redirect(url_for("cart"))


@app.get("/cart")
def cart():
    user = need_user()
    items = CARTS.get(user, {})
    rows = "".join(
        f'<li class="cart-row">{next(p["name"] for p in PRODUCTS if p["id"] == pid)} x {qty}</li>'
        for pid, qty in items.items()
    )
    action = '<a id="checkout" href="/checkout/address">Checkout</a>' if items else "<p>Your cart is empty</p>"
    return page("Cart", f"<h1>Cart</h1><ul>{rows}</ul>{action}")


@app.route("/checkout/address", methods=["GET", "POST"])
def checkout_address():
    user = need_user()
    if not CARTS.get(user):
        return redirect(url_for("cart"))
    if request.method == "POST":
        street = request.form.get("street", "").strip()
        if not street:
            return page("Address", "<p>Street is required</p>"), 400
        PENDING[user] = {"street": street[:200]}
        return redirect(url_for("checkout_payment"))
    return page(
        "Address",
        '<h1>Delivery address</h1><form method="post"><label>Street <input name="street"></label>'
        '<button id="address-btn">Continue</button></form>',
    )


@app.route("/checkout/payment", methods=["GET", "POST"])
def checkout_payment():
    user = need_user()
    if user not in PENDING:
        return redirect(url_for("checkout_address"))
    if request.method == "POST":
        coupon = request.form.get("coupon", "").strip()
        discount = {"SAVE10": 0.1}.get(coupon.upper(), 0.0)
        if coupon and not discount:
            code = coupon.encode("ascii")[:16].decode("ascii")  # fails on a coupon that is not plain ASCII
            discount = 0.05 if code.startswith("VIP") else 0.0
        PENDING[user].update(card=request.form.get("card", "")[-4:], discount=discount)
        return redirect(url_for("checkout_confirm"))
    return page(
        "Payment",
        '<h1>Payment</h1><form method="post"><label>Card <input name="card"></label>'
        '<label>Coupon <input name="coupon"></label><button id="pay-btn">Continue</button></form>',
    )


@app.route("/checkout/confirm", methods=["GET", "POST"])
def checkout_confirm():
    user = need_user()
    pending = PENDING.get(user)
    if pending is None or "card" not in pending:
        return redirect(url_for("checkout_payment"))
    items = CARTS.get(user, {})
    if request.method == "POST":
        total = sum(next(p["price"] for p in PRODUCTS if p["id"] == pid) * qty for pid, qty in items.items())
        count = sum(items.values())
        average = total / count  # an order of only zero quantities divides by zero
        ORDERS.append(
            {
                "user": user,
                "total": round(total * (1 - pending["discount"]), 2),
                "avg": average,
                "ref": request.form.get("ref", ""),
            }
        )
        CARTS[user] = {}
        return redirect(url_for("orders"))
    return page(
        "Confirm",
        f"<h1>Confirm order</h1><p>Deliver to {escape(pending['street'])}</p>"
        '<form method="post"><input type="hidden" name="ref" value="web">'
        '<button id="place-btn">Place order</button></form>',
    )


@app.get("/orders")
def orders():
    user = need_user()
    rows = "".join(
        f'<li class="order-row">Order #{i + 1}: ${o["total"]}</li>'
        for i, o in enumerate(o for o in ORDERS if o["user"] == user)
    )
    return page("Orders", f"<h1>Your orders</h1><ul>{rows or '<li>No orders yet</li>'}</ul>")


if __name__ == "__main__":
    socket.getfqdn = lambda name="": name or "localhost"  # type: ignore[assignment]
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5088")), threaded=True)
