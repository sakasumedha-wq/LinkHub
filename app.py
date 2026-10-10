
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, abort
)
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import os
import re
from urllib.parse import urlparse

app = Flask(__name__)
app.secret_key = os.environ.get(
    "LINKHUB_SECRET_KEY",
    "dev-only-change-this-before-deployment"
)

DATABASE = os.path.join(os.path.dirname(__file__), "linkhub.db")


def get_db():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'Student Developer',
                bio TEXT NOT NULL DEFAULT 'Welcome to my LinkHub!',
                github_url TEXT NOT NULL DEFAULT '',
                linkedin_url TEXT NOT NULL DEFAULT '',
                instagram_url TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Add missing columns if the database already exists.
        columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(users)").fetchall()
        }

        for column in ("github_url", "linkedin_url", "instagram_url"):
            if column not in columns:
                db.execute(
                    f"ALTER TABLE users ADD COLUMN {column} "
                    "TEXT NOT NULL DEFAULT ''"
                )


def current_user():
    user_id = session.get("user_id")

    if not user_id:
        return None

    with get_db() as db:
        return db.execute("""
            SELECT id, username, name, role, bio,
                   github_url, linkedin_url, instagram_url
            FROM users
            WHERE id = ?
        """, (user_id,)).fetchone()


def valid_profile_url(value, allowed_hosts):
    """Accept an empty field or a valid HTTPS profile URL."""
    if not value:
        return True

    try:
        parsed = urlparse(value)
        hostname = (parsed.hostname or "").lower()

        return (
            parsed.scheme == "https"
            and hostname in allowed_hosts
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
        )
    except ValueError:
        return False


@app.route("/")
def home():
    return render_template("home.html", user=current_user())


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "")

        if not re.fullmatch(r"[a-z0-9_]{3,30}", username):
            flash(
                "Username must be 3-30 characters: "
                "letters, numbers or underscores."
            )
            return render_template("register.html")

        if not name or len(name) > 80:
            flash("Enter a name with at most 80 characters.")
            return render_template("register.html")

        if len(password) < 8:
            flash("Password must contain at least 8 characters.")
            return render_template("register.html")

        try:
            with get_db() as db:
                cursor = db.execute("""
                    INSERT INTO users (username, name, password_hash)
                    VALUES (?, ?, ?)
                """, (
                    username,
                    name,
                    generate_password_hash(password)
                ))
                user_id = cursor.lastrowid

            session.clear()
            session["user_id"] = user_id
            return redirect(url_for("dashboard"))

        except sqlite3.IntegrityError:
            flash("That username is already taken. Choose another.")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")

        with get_db() as db:
            user = db.execute("""
                SELECT id, password_hash
                FROM users
                WHERE username = ?
            """, (username,)).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("dashboard"))

        flash("Invalid username or password.")

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/dashboard")
def dashboard():
    user = current_user()

    if not user:
        return redirect(url_for("login"))

    return render_template("dashboard.html", user=user)


@app.route("/edit", methods=["GET", "POST"])
def edit_profile():
    user = current_user()

    if not user:
        return redirect(url_for("login"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        role = request.form.get("role", "").strip()
        bio = request.form.get("bio", "").strip()

        github_url = request.form.get("github_url", "").strip()
        linkedin_url = request.form.get("linkedin_url", "").strip()
        instagram_url = request.form.get("instagram_url", "").strip()

        if not name or not role or not bio:
            flash("Please fill in your name, role and bio.")
            return render_template("edit.html", user=user)

        if len(name) > 80 or len(role) > 100 or len(bio) > 500:
            flash("Name, role or bio exceeds its allowed length.")
            return render_template("edit.html", user=user)

        if any(len(link) > 300 for link in (
            github_url, linkedin_url, instagram_url
        )):
            flash("A social link is too long.")
            return render_template("edit.html", user=user)

        if not valid_profile_url(
            github_url, {"github.com", "www.github.com"}
        ):
            flash("Enter a valid HTTPS GitHub profile URL.")
            return render_template("edit.html", user=user)

        if not valid_profile_url(
            linkedin_url, {"linkedin.com", "www.linkedin.com"}
        ):
            flash("Enter a valid HTTPS LinkedIn profile URL.")
            return render_template("edit.html", user=user)

        if not valid_profile_url(
            instagram_url, {"instagram.com", "www.instagram.com"}
        ):
            flash("Enter a valid HTTPS Instagram profile URL.")
            return render_template("edit.html", user=user)

        with get_db() as db:
            db.execute("""
                UPDATE users
                SET name = ?,
                    role = ?,
                    bio = ?,
                    github_url = ?,
                    linkedin_url = ?,
                    instagram_url = ?
                WHERE id = ?
            """, (
                name,
                role,
                bio,
                github_url,
                linkedin_url,
                instagram_url,
                user["id"]
            ))

        flash("Your profile has been updated!")
        return redirect(url_for("dashboard"))

    return render_template("edit.html", user=user)


@app.route("/u/<username>")
def public_profile(username):
    with get_db() as db:
        profile = db.execute("""
            SELECT username, name, role, bio,
                   github_url, linkedin_url, instagram_url
            FROM users
            WHERE username = ?
        """, (username.lower(),)).fetchone()

    if not profile:
        abort(404)

    logged_in_user = current_user()
    is_owner = (
        logged_in_user is not None
        and logged_in_user["username"] == profile["username"]
    )

    return render_template(
        "index.html",
        profile=profile,
        is_owner=is_owner
    )


@app.errorhandler(404)
def not_found(error):
    return render_template("not_found.html"), 404


init_db()

if __name__ == "__main__":
    app.run(debug=True)
