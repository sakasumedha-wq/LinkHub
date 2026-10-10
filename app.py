
import os
import re
import sqlite3
import io
import qrcode

from urllib.parse import urlparse
from flask import (
    Flask, abort, flash, redirect, render_template,
    request, session, url_for, send_file
)
from werkzeug.security import check_password_hash, generate_password_hash


# --------------------------------------------------
# APP CONFIGURATION
# --------------------------------------------------

app = Flask(__name__)

app.secret_key = os.environ.get("LINKHUB_SECRET_KEY")

if not app.secret_key:
    raise RuntimeError(
        "Set the LINKHUB_SECRET_KEY environment variable before starting LinkHub."
    )

DATABASE = os.environ.get(
    "LINKHUB_DATABASE_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "linkhub.db")
)


# --------------------------------------------------
# DATABASE
# Existing database and user data are preserved.
# --------------------------------------------------

def get_db():
    folder = os.path.dirname(os.path.abspath(DATABASE))
    os.makedirs(folder, exist_ok=True)

    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    return connection


def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT '',
                bio TEXT DEFAULT '',
                github_url TEXT DEFAULT '',
                linkedin_url TEXT DEFAULT '',
                instagram_url TEXT DEFAULT '',
                resume_url TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        columns = {
            row["name"]
            for row in db.execute(
                "PRAGMA table_info(users)"
            ).fetchall()
        }

        migrations = {
            "role": "TEXT DEFAULT ''",
            "bio": "TEXT DEFAULT ''",
            "github_url": "TEXT DEFAULT ''",
            "linkedin_url": "TEXT DEFAULT ''",
            "instagram_url": "TEXT DEFAULT ''",
            "resume_url": "TEXT DEFAULT ''",
        }

        for column, definition in migrations.items():
            if column not in columns:
                db.execute(
                    f"ALTER TABLE users ADD COLUMN {column} {definition}"
                )

        db.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                technologies TEXT DEFAULT '',
                github_url TEXT DEFAULT '',
                demo_url TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id)
                    REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS skills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                UNIQUE (user_id, name),
                FOREIGN KEY (user_id)
                    REFERENCES users(id) ON DELETE CASCADE
            )
        """)


# --------------------------------------------------
# HELPERS
# --------------------------------------------------

def current_user():
    user_id = session.get("user_id")

    if not user_id:
        return None

    with get_db() as db:
        user = db.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()

    if user is None:
        session.clear()

    return user


def valid_url(value, allowed_hosts=None):
    """Accept empty optional URLs or valid HTTPS URLs."""
    if not value:
        return True

    if len(value) > 500:
        return False

    try:
        parsed = urlparse(value)
        hostname = (parsed.hostname or "").lower()

        if parsed.scheme != "https" or not hostname or not parsed.netloc:
            return False

        if parsed.username or parsed.password:
            return False

        if parsed.port not in (None, 443):
            return False

        if allowed_hosts and not any(
            hostname == host or hostname.endswith("." + host)
            for host in allowed_hosts
        ):
            return False

        return True

    except ValueError:
        return False


GITHUB_HOSTS = {"github.com"}
LINKEDIN_HOSTS = {"linkedin.com"}
INSTAGRAM_HOSTS = {"instagram.com"}


# --------------------------------------------------
# PORTFOLIO READINESS SCORE
# Calculated from existing data; never stored in DB.
# Total possible score: 100 points.
# --------------------------------------------------

def calculate_portfolio_readiness(user, projects, skills):
    score = 0
    checks = []

    def add_check(title, points, completed, suggestion):
        nonlocal score

        completed = bool(completed)

        if completed:
            score += points

        checks.append({
            "title": title,
            "points": points,
            "completed": completed,
            "suggestion": suggestion
        })

    # 1. Basic profile: 20 points
    add_check(
        "Full name",
        10,
        bool((user["name"] or "").strip()),
        "Add your name to identify your portfolio."
    )

    add_check(
        "Professional role",
        10,
        bool((user["role"] or "").strip()),
        "Add a role such as AI/ML Student or Web Developer."
    )

    # 2. Bio: 15 points
    add_check(
        "About you",
        15,
        len((user["bio"] or "").strip()) >= 50,
        "Write an informative bio of at least 50 characters."
    )

    # 3. Skills: 15 points
    add_check(
        "Skills",
        15,
        len(skills) >= 3,
        "Add at least 3 skills you can demonstrate."
    )

    # 4. Projects: 20 points
    add_check(
        "Projects",
        20,
        len(projects) >= 2,
        "Add at least 2 projects to showcase your work."
    )

    # 5. Project descriptions: 10 points
    projects_with_descriptions = sum(
        1 for project in projects
        if len((project["description"] or "").strip()) >= 30
    )

    add_check(
        "Project descriptions",
        10,
        len(projects) > 0
        and projects_with_descriptions == len(projects),
        "Write a meaningful description for every project."
    )

    # 6. Technologies: 10 points
    projects_with_technologies = sum(
        1 for project in projects
        if (project["technologies"] or "").strip()
    )

    add_check(
        "Technologies used",
        10,
        len(projects) > 0
        and projects_with_technologies == len(projects),
        "List the technologies used in each project."
    )

    # 7. GitHub or LinkedIn: 5 points
    has_professional_link = any(
        (user[field] or "").strip()
        for field in ("github_url", "linkedin_url")
    )

    add_check(
        "Professional links",
        5,
        has_professional_link,
        "Add your GitHub or LinkedIn profile."
    )

    # 8. Resume: 5 points
    add_check(
        "Resume link",
        5,
        bool((user["resume_url"] or "").strip()),
        "Add a publicly accessible link to your resume."
    )

    if score >= 80:
        level = "Strong portfolio"
        message = "Great progress! Review any remaining suggestions."
    elif score >= 60:
        level = "Good start"
        message = "Your portfolio is developing. A few improvements can help."
    elif score >= 40:
        level = "Getting started"
        message = "Complete more sections to strengthen your portfolio."
    else:
        level = "Needs improvement"
        message = "Start with your profile details, skills, and projects."

    return {
        "score": score,
        "level": level,
        "message": message,
        "checks": checks,
        "completed_count": sum(
            1 for check in checks if check["completed"]
        ),
        "total_checks": len(checks)
    }


# --------------------------------------------------
# HOME
# --------------------------------------------------

@app.route("/")
def home():
    return render_template("home.html")


# --------------------------------------------------
# REGISTER
# --------------------------------------------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "")

        if not re.fullmatch(r"[a-z0-9_]{3,30}", username):
            flash(
                "Username must be 3–30 characters using letters, "
                "numbers, or underscores.",
                "error"
            )
            return render_template("register.html")

        if not name or len(name) > 100:
            flash("Enter a valid name.", "error")
            return render_template("register.html")

        if len(password) < 8:
            flash("Password must contain at least 8 characters.", "error")
            return render_template("register.html")

        password_hash = generate_password_hash(password)

        try:
            with get_db() as db:
                db.execute(
                    """
                    INSERT INTO users (username, name, password_hash)
                    VALUES (?, ?, ?)
                    """,
                    (username, name, password_hash)
                )

            flash("Account created successfully. Please log in.", "success")
            return redirect(url_for("login"))

        except sqlite3.IntegrityError:
            flash("That username is already taken.", "error")

    return render_template("register.html")


# --------------------------------------------------
# LOGIN
# --------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")

        with get_db() as db:
            user = db.execute(
                "SELECT * FROM users WHERE username = ?",
                (username,)
            ).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            flash("Welcome back!", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid username or password.", "error")

    return render_template("login.html")


# --------------------------------------------------
# LOGOUT
# --------------------------------------------------

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("home"))


# --------------------------------------------------
# DASHBOARD
# --------------------------------------------------

@app.route("/dashboard")
def dashboard():
    user = current_user()

    if not user:
        return redirect(url_for("login"))

    with get_db() as db:
        projects = db.execute(
            """
            SELECT * FROM projects
            WHERE user_id = ?
            ORDER BY id DESC
            """,
            (user["id"],)
        ).fetchall()

        skills = db.execute(
            """
            SELECT * FROM skills
            WHERE user_id = ?
            ORDER BY name COLLATE NOCASE
            """,
            (user["id"],)
        ).fetchall()

    readiness = calculate_portfolio_readiness(
        user, projects, skills
    )

    return render_template(
        "dashboard.html",
        user=user,
        projects=projects,
        skills=skills,
        readiness=readiness
    )


# --------------------------------------------------
# EDIT PROFILE
# --------------------------------------------------

@app.route("/edit-profile", methods=["GET", "POST"])
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
        resume_url = request.form.get("resume_url", "").strip()

        if not name or len(name) > 100:
            flash("Name is required and must be under 100 characters.", "error")

        elif len(role) > 150 or len(bio) > 1000:
            flash("Your role or bio is too long.", "error")

        elif not valid_url(github_url, GITHUB_HOSTS):
            flash("Enter a valid HTTPS GitHub URL.", "error")

        elif not valid_url(linkedin_url, LINKEDIN_HOSTS):
            flash("Enter a valid HTTPS LinkedIn URL.", "error")

        elif not valid_url(instagram_url, INSTAGRAM_HOSTS):
            flash("Enter a valid HTTPS Instagram URL.", "error")

        elif not valid_url(resume_url):
            flash("Resume must be a valid HTTPS link.", "error")

        else:
            with get_db() as db:
                db.execute(
                    """
                    UPDATE users
                    SET name = ?, role = ?, bio = ?, github_url = ?,
                        linkedin_url = ?, instagram_url = ?, resume_url = ?
                    WHERE id = ?
                    """,
                    (
                        name, role, bio, github_url, linkedin_url,
                        instagram_url, resume_url, user["id"]
                    )
                )

            flash("Profile updated successfully!", "success")
            return redirect(url_for("dashboard"))

    return render_template("edit.html", user=user)


# --------------------------------------------------
# ADD PROJECT
# --------------------------------------------------

@app.route("/projects/add", methods=["POST"])
def add_project():
    user = current_user()

    if not user:
        return redirect(url_for("login"))

    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    technologies = request.form.get("technologies", "").strip()
    github_url = request.form.get("github_url", "").strip()
    demo_url = request.form.get("demo_url", "").strip()

    if not title or len(title) > 100:
        flash("Project title is required and must be under 100 characters.", "error")

    elif len(description) > 1000 or len(technologies) > 300:
        flash("Project description or technologies are too long.", "error")

    elif not valid_url(github_url, GITHUB_HOSTS):
        flash("Enter a valid HTTPS GitHub project URL.", "error")

    elif not valid_url(demo_url):
        flash("Demo URL must be a valid HTTPS link.", "error")

    else:
        with get_db() as db:
            db.execute(
                """
                INSERT INTO projects (
                    user_id, title, description, technologies,
                    github_url, demo_url
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user["id"], title, description, technologies,
                    github_url, demo_url
                )
            )

        flash("Project added successfully!", "success")

    return redirect(url_for("dashboard"))


# --------------------------------------------------
# EDIT PROJECT
# --------------------------------------------------

@app.route("/projects/<int:project_id>/edit", methods=["POST"])
def edit_project(project_id):
    user = current_user()

    if not user:
        return redirect(url_for("login"))

    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    technologies = request.form.get("technologies", "").strip()
    github_url = request.form.get("github_url", "").strip()
    demo_url = request.form.get("demo_url", "").strip()

    if not title or len(title) > 100:
        flash("Project title is required and must be under 100 characters.", "error")

    elif len(description) > 1000 or len(technologies) > 300:
        flash("Project description or technologies are too long.", "error")

    elif not valid_url(github_url, GITHUB_HOSTS):
        flash("Enter a valid HTTPS GitHub project URL.", "error")

    elif not valid_url(demo_url):
        flash("Demo URL must be a valid HTTPS link.", "error")

    else:
        with get_db() as db:
            result = db.execute(
                """
                UPDATE projects
                SET title = ?, description = ?, technologies = ?,
                    github_url = ?, demo_url = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    title, description, technologies, github_url,
                    demo_url, project_id, user["id"]
                )
            )

        if result.rowcount:
            flash("Project updated successfully!", "success")
        else:
            flash("Project not found.", "error")

    return redirect(url_for("dashboard"))


# --------------------------------------------------
# DELETE PROJECT
# --------------------------------------------------

@app.route("/projects/<int:project_id>/delete", methods=["POST"])
def delete_project(project_id):
    user = current_user()

    if not user:
        return redirect(url_for("login"))

    with get_db() as db:
        result = db.execute(
            "DELETE FROM projects WHERE id = ? AND user_id = ?",
            (project_id, user["id"])
        )

    if result.rowcount:
        flash("Project deleted successfully.", "success")
    else:
        flash("Project not found.", "error")

    return redirect(url_for("dashboard"))


# --------------------------------------------------
# ADD SKILL
# --------------------------------------------------

@app.route("/skills/add", methods=["POST"])
def add_skill():
    user = current_user()

    if not user:
        return redirect(url_for("login"))

    skill_name = request.form.get("name", "").strip()

    if not skill_name or len(skill_name) > 50:
        flash("Skill name is required and must be under 50 characters.", "error")
        return redirect(url_for("dashboard"))

    try:
        with get_db() as db:
            db.execute(
                "INSERT INTO skills (user_id, name) VALUES (?, ?)",
                (user["id"], skill_name)
            )

        flash("Skill added successfully!", "success")

    except sqlite3.IntegrityError:
        flash("You have already added that skill.", "error")

    return redirect(url_for("dashboard"))


# --------------------------------------------------
# DELETE SKILL
# --------------------------------------------------

@app.route("/skills/<int:skill_id>/delete", methods=["POST"])
def delete_skill(skill_id):
    user = current_user()

    if not user:
        return redirect(url_for("login"))

    with get_db() as db:
        result = db.execute(
            "DELETE FROM skills WHERE id = ? AND user_id = ?",
            (skill_id, user["id"])
        )

    if result.rowcount:
        flash("Skill deleted successfully.", "success")
    else:
        flash("Skill not found.", "error")

    return redirect(url_for("dashboard"))


# --------------------------------------------------
# PUBLIC PORTFOLIO
# --------------------------------------------------

@app.route("/u/<username>")
def public_profile(username):
    with get_db() as db:
        user = db.execute(
            "SELECT * FROM users WHERE username = ?",
            (username.lower(),)
        ).fetchone()

        if user is None:
            abort(404)

        projects = db.execute(
            """
            SELECT * FROM projects
            WHERE user_id = ?
            ORDER BY id DESC
            """,
            (user["id"],)
        ).fetchall()

        skills = db.execute(
            """
            SELECT * FROM skills
            WHERE user_id = ?
            ORDER BY name COLLATE NOCASE
            """,
            (user["id"],)
        ).fetchall()

    return render_template(
        "index.html",
        user=user,
        projects=projects,
        skills=skills
    )


# --------------------------------------------------
# PERSONAL QR CODE
# --------------------------------------------------

@app.route("/u/<username>/qr.png")
def profile_qr(username):
    """Generate a downloadable QR code for a user's public portfolio."""

    with get_db() as db:
        user = db.execute(
            "SELECT username FROM users WHERE username = ?",
            (username.lower(),)
        ).fetchone()

    if user is None:
        abort(404)

    portfolio_url = url_for(
        "public_profile",
        username=user["username"],
        _external=True
    )

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4
    )

    qr.add_data(portfolio_url)
    qr.make(fit=True)

    image = qr.make_image(
        fill_color="#17132b",
        back_color="white"
    )

    image_buffer = io.BytesIO()
    image.save(image_buffer, format="PNG")
    image_buffer.seek(0)

    return send_file(
        image_buffer,
        mimetype="image/png",
        download_name=f"{user['username']}-linkhub-qr.png"
    )


# --------------------------------------------------
# NOT FOUND
# --------------------------------------------------

@app.errorhandler(404)
def page_not_found(error):
    return render_template("not_found.html"), 404


# --------------------------------------------------
# INITIALIZE EXISTING DATABASE TABLES
# --------------------------------------------------

init_db()


if __name__ == "__main__":
    app.run(debug=False)
