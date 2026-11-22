"""A small Flask app with planted bugs, used as Sentri's test target and demo.

Each bug is behind an env flag: FIX_<NAME>=1 turns it off, so tests can show "fixed" and "regression"
without editing code. State lives in memory and is re-seeded on every start (clean seeded state).
"""

from __future__ import annotations

import os
import secrets
import tempfile
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template_string, request, session, url_for

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)  # random per start; nothing hard-coded


def fixed(name: str) -> bool:
    return os.environ.get(f"FIX_{name}") == "1"


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
FLAKY_STATE = Path(os.environ.get("FLAKY_STATE", Path(tempfile.gettempdir()) / "sentri-buggy-flaky.txt"))

LOCK = threading.Lock()
USERS: list[dict] = []  # no built-in accounts: tests sign up a fresh random user
TASKS: list[dict] = []
COMMENTS: list[dict] = [{"id": 1, "text": "Welcome to the board"}]
USED_NONCES: set[str] = set()
TASK_REQUESTS: dict[int, list[float]] = {}

BASE = """<!doctype html><html><head><meta charset="utf-8"><title>{{ title }}</title>
<style>body{font-family:system-ui;max-width:680px;margin:32px auto;padding:0 16px}
input,textarea,button{display:block;margin:8px 0;padding:6px}nav a{margin-right:12px}</style></head>
<body><nav><a href="/">Home</a><a href="/signup">Sign up</a><a href="/users">Users</a><a href="/login">Log in</a>
<a href="/tasks">Tasks</a><a href="/comments">Comments</a><a href="/profile">Profile</a></nav>
{% for m in messages %}<p class="flash">{{ m }}</p>{% endfor %}
{{ body|safe }}</body></html>"""


def page(title: str, body: str, **ctx) -> str:
    inner = render_template_string(body, **ctx)
    messages = session.pop("flash", [])
    return render_template_string(BASE, title=title, body=inner, messages=messages)


def flash(msg: str) -> None:
    session["flash"] = [*session.get("flash", []), msg]


@app.get("/")
def home():
    return page("Home", "<h1>Buggy demo app</h1><p>A target for Sentri.</p>")


@app.get("/favicon.ico")
def favicon():
    abort(404)


# ---- signup: fetch-based form -------------------------------------------------------------------------
SIGNUP = """<h1>Sign up</h1>
<form id="signup" onsubmit="return false">
  <label>Email <input name="email" type="email"></label>
  <label>Password <input name="password" type="password"></label>
  <button type="submit" id="signup-btn">Create account</button>
</form><p id="msg"></p>
<script>
const btn = document.getElementById('signup-btn');
btn.addEventListener('click', async () => {
  {% if guard %}if (btn.disabled) return; btn.disabled = true;{% endif %}
  const f = document.getElementById('signup');
  const r = await fetch('/api/signup', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({email: f.email.value, password: f.password.value})});
  if (r.ok) { location.href = '/users'; return; }
  document.getElementById('msg').textContent = 'Could not sign up';
  {% if guard %}btn.disabled = false;{% endif %}
});
</script>"""


@app.get("/signup")
def signup_page():
    return page("Sign up", SIGNUP, guard=fixed("SIGNUP_DOUBLE"))


@app.post("/api/signup")
def api_signup():
    data = request.get_json(force=True)
    email = data.get("email", "")
    if fixed("SIGNUP_EMPTY") and not email.strip():
        return jsonify(error="email required"), 400
    domain = email.split("@")[1]  # BUG: blank email -> IndexError -> 500
    time.sleep(0.3)  # "password hashing": makes the double-submit window realistic
    with LOCK:
        if fixed("SIGNUP_DOUBLE") and any(u["email"] == email for u in USERS):
            return jsonify(error="exists"), 409
        USERS.append({"id": len(USERS) + 1, "email": email, "password": data.get("password", ""), "name": domain})
    return jsonify(ok=True)


@app.get("/users")
def users_page():
    rows = "".join(f'<li data-testid="user-row">{u["email"]}</li>' for u in USERS)
    return page("Users", f"<h1>Users</h1><ul>{rows}</ul>")


# ---- login --------------------------------------------------------------------------------------------
LOGIN = """<h1>Log in</h1><form method="post" action="/login">
<label>Email <input name="email" type="email"></label>
<label>Password <input name="password" type="password"></label>
<button type="submit">Log in</button></form>"""


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = next(
            (
                u
                for u in USERS
                if u["email"] == request.form.get("email") and u["password"] == request.form.get("password")
            ),
            None,
        )
        if user is None:
            return page("Log in", "<p>Wrong email or password</p>" + LOGIN), 401
        session["user_id"] = user["id"]
        return redirect(url_for("tasks_page"))
    return page("Log in", LOGIN)


def current_user() -> dict | None:
    uid = session.get("user_id")
    return next((u for u in USERS if u["id"] == uid), None)


# ---- tasks: classic form post with upload -------------------------------------------------------------
NEW_TASK = """<h1>New task</h1><form method="post" action="/tasks/new" enctype="multipart/form-data">
<input type="hidden" name="nonce" value="{{ nonce }}">
<label>Title <input name="title"></label>
<label>Attachment <input type="file" name="file"></label>
<button id="save" type="submit">Save task</button></form>"""

TASKS_PAGE = """<h1>Tasks</h1><p id="stats">loading stats...</p>
<ul>{% for t in tasks %}<li data-testid="task-row">{{ t.title }} ({{ t.size }} bytes)</li>{% endfor %}</ul>
<a href="/tasks/new">New task</a>
<script>
const ctl = new AbortController();
setTimeout(() => ctl.abort(), 1500);
{% if guard %}
fetch('/api/stats', {signal: ctl.signal}).then(r => r.json())
  .then(d => { document.getElementById('stats').textContent = d.count + ' tasks'; })
  .catch(() => { document.getElementById('stats').textContent = 'stats unavailable'; });
{% else %}
// BUG: no catch; a slow network aborts the request -> unhandled promise rejection
fetch('/api/stats', {signal: ctl.signal}).then(r => r.json())
  .then(d => { document.getElementById('stats').textContent = d.count + ' tasks'; });
{% endif %}
</script>"""


@app.get("/tasks")
def tasks_page():
    if current_user() is None:
        return redirect(url_for("login"))
    return page("Tasks", TASKS_PAGE, tasks=TASKS, guard=fixed("TASKS_SLOW"))


@app.get("/api/stats")
def stats():
    return jsonify(count=len(TASKS))


@app.get("/tasks/new")
def new_task_page():
    if current_user() is None:
        return redirect(url_for("login"))
    return page("New task", NEW_TASK, nonce=uuid.uuid4().hex)


@app.post("/tasks/new")
def create_task():
    if fixed("TASK_SESSION") and current_user() is None:
        return redirect(url_for("login"))
    user = USERS[session["user_id"] - 1]  # BUG: expired session -> KeyError -> 500

    now = time.monotonic()
    with LOCK:
        recent = [t for t in TASK_REQUESTS.get(user["id"], []) if now - t < 1.0] + [now]
        TASK_REQUESTS[user["id"]] = recent
    if len(recent) > 2 and not fixed("TASK_RAPID"):
        raise RuntimeError("database is locked")  # BUG: burst of writes -> 500

    title = request.form.get("title", "").strip()
    if not title:
        return page("New task", "<p>Title is required</p>"), 400
    upload = request.files.get("file")
    data = upload.read() if upload else b""
    if fixed("TASK_FILE"):
        if not data or data[:8] != PNG_MAGIC or len(data) > 5 * 1024 * 1024:
            return page("New task", "<p>Please upload a PNG under 5 MB</p>"), 400
    else:
        if data[0] != PNG_MAGIC[0]:  # BUG: 0-byte file -> IndexError -> 500
            raise ValueError("not a png file")  # BUG: corrupt file -> 500
    time.sleep(0.3)  # "thumbnail generation"
    with LOCK:
        nonce = request.form.get("nonce", "")
        if nonce in USED_NONCES:  # idempotency: a double submit is rejected, not duplicated
            return page("New task", "<p>Already saved</p>"), 409
        USED_NONCES.add(nonce)
        TASKS.append({"id": len(TASKS) + 1, "title": title, "size": len(data), "owner": user["id"]})
    flash("Task created")
    return redirect(url_for("tasks_page"))


# ---- comments: fetch-based, guarded button, flaky blank handling --------------------------------------
COMMENTS_PAGE = """<h1>Comments</h1>
<ul>{% for c in comments %}<li data-testid="comment-row">{{ c.text }}</li>{% endfor %}</ul>
<form id="cf" onsubmit="return false"><label>Comment <textarea name="text"></textarea></label>
<button type="submit" id="comment-btn">Post comment</button></form><p id="cmsg"></p>
<script>
const b = document.getElementById('comment-btn');
b.addEventListener('click', async () => {
  if (b.disabled) return; b.disabled = true;
  const r = await fetch('/api/comments', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text: document.getElementById('cf').text.value})});
  if (r.ok) { location.reload(); return; }
  document.getElementById('cmsg').textContent = 'Comment not posted'; b.disabled = false;
});
</script>"""


def _flaky_tick() -> int:
    """Counter that survives restarts, so a blank comment fails on exactly every third request."""
    with LOCK:
        n = int(FLAKY_STATE.read_text()) + 1 if FLAKY_STATE.exists() else 1
        FLAKY_STATE.write_text(str(n))
    return n


@app.get("/comments")
def comments_page():
    return page("Comments", COMMENTS_PAGE, comments=COMMENTS)


@app.post("/api/comments")
def api_comments():
    text = (request.get_json(force=True) or {}).get("text", "")
    if not text.strip():
        if not fixed("COMMENT_FLAKY") and _flaky_tick() % 3 == 0:
            raise RuntimeError("comment index out of sync")  # BUG: fails 1 in 3 -> flaky
        return jsonify(error="text required"), 400
    with LOCK:
        COMMENTS.append({"id": len(COMMENTS) + 1, "text": text})
    return jsonify(ok=True)


# ---- profile: the clean flow --------------------------------------------------------------------------
PROFILE = """<h1>Profile</h1><form id="pf" onsubmit="return false">
<label>Display name <input name="display_name" value="{{ name }}"></label>
<button type="submit" id="profile-btn">Save profile</button></form><p id="pmsg"></p>
<script>
const pb = document.getElementById('profile-btn');
pb.addEventListener('click', async () => {
  if (pb.disabled) return; pb.disabled = true;
  try {
    const r = await fetch('/api/profile', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({display_name: document.getElementById('pf').display_name.value})});
    document.getElementById('pmsg').textContent = r.ok ? 'Profile saved' :
      (r.status === 401 ? 'Please log in again' : 'Please enter a name');
  } catch (e) { document.getElementById('pmsg').textContent = 'Network problem, try again'; }
  pb.disabled = false;
});
</script>"""


@app.get("/profile")
def profile_page():
    user = current_user()
    if user is None:
        return redirect(url_for("login"))
    return page("Profile", PROFILE, name=user["name"])


@app.post("/api/profile")
def api_profile():
    user = current_user()
    if user is None:
        return jsonify(error="login required"), 401
    name = ((request.get_json(force=True) or {}).get("display_name") or "").strip()
    if not name:
        return jsonify(error="name required"), 400
    user["name"] = name[:80]
    return jsonify(ok=True)


if __name__ == "__main__":
    import socket

    # http.server calls socket.getfqdn() while binding; on some macOS machines (GitHub runners included) the
    # reverse DNS lookup for 127.0.0.1 takes ~35 s. This demo only binds locally, so skip the lookup.
    socket.getfqdn = lambda name="": name or "localhost"  # type: ignore[assignment]
    port = int(os.environ.get("PORT", "5055"))
    app.run(host="127.0.0.1" if os.environ.get("BIND_ALL") != "1" else "0.0.0.0", port=port, threaded=True)
