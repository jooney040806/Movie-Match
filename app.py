
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from dotenv import load_dotenv
import requests
import secrets
import string
import sqlite3
import os

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

TMDB_TOKEN = os.getenv("TMDB_TOKEN")
DB_PATH = "moviematch.db"


# DATABASE

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def init_db():
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS rooms (
                code TEXT PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS members (
                id TEXT PRIMARY KEY,
                room_code TEXT NOT NULL,
                FOREIGN KEY (room_code) REFERENCES rooms(code),
                UNIQUE(id, room_code)
            );

            CREATE TABLE IF NOT EXISTS votes (
                member_id TEXT NOT NULL,
                room_code TEXT NOT NULL,
                movie_id INTEGER NOT NULL,
                liked INTEGER NOT NULL,
                PRIMARY KEY (member_id, room_code, movie_id),
                FOREIGN KEY (member_id) REFERENCES members(id),
                FOREIGN KEY (room_code) REFERENCES rooms(code)
            );
        """)


init_db()


# HELPERS

def generate_code():
    characters = string.ascii_uppercase + string.digits

    with get_db() as db:
        while True:
            code = "".join(
                secrets.choice(characters) for _ in range(6)
            )

            exists = db.execute(
                "SELECT code FROM rooms WHERE code = ?", (code,)
            ).fetchone()

            if not exists:
                return code


def join_member(code):
    member_id = secrets.token_hex(16)

    with get_db() as db:
        count = db.execute(
            "SELECT COUNT(*) FROM members WHERE room_code = ?",
            (code,)
        ).fetchone()[0]

        if count >= 2:
            return False

        db.execute(
            "INSERT INTO members (id, room_code) VALUES (?, ?)",
            (member_id, code)
        )

    session["member_id"] = member_id
    session["room_code"] = code
    return True


def current_member(code):
    member_id = session.get("member_id")

    if session.get("room_code") != code or not member_id:
        return None

    with get_db() as db:
        member = db.execute(
            "SELECT id FROM members WHERE id = ? AND room_code = ?",
            (member_id, code)
        ).fetchone()

    return member_id if member else None


def fetch_movies():
    if not TMDB_TOKEN:
        return []

    url = "https://api.themoviedb.org/3/movie/popular"

    headers = {
        "Authorization": f"Bearer {TMDB_TOKEN}"
    }

    movies = []

    try:
        for page in range(1, 6):
            response = requests.get(
                url,
                headers=headers,
                params={"language": "en-US", "page": page},
                timeout=10
            )
            response.raise_for_status()

            movies.extend(response.json().get("results", []))

    except requests.RequestException:
        return []

    return movies


# HOME

@app.route("/")
def home():
    return render_template("index.html")


# CREATE ROOM

@app.route("/create", methods=["POST"])
def create_room():
    code = generate_code()

    with get_db() as db:
        db.execute(
            "INSERT INTO rooms (code) VALUES (?)",
            (code,)
        )

    join_member(code)

    return redirect(url_for("room", code=code))


# JOIN ROOM

@app.route("/join", methods=["GET", "POST"])
def join_room():
    if request.method == "GET":
        return render_template("join.html")

    code = request.form.get("code", "").strip().upper()

    with get_db() as db:
        room_exists = db.execute(
            "SELECT code FROM rooms WHERE code = ?",
            (code,)
        ).fetchone()

    if not room_exists:
        return render_template(
            "join.html", error="Room not found!"
        )

    if current_member(code):
        return redirect(url_for("room", code=code))

    if not join_member(code):
        return render_template(
            "join.html", error="This room is full!"
        )

    return redirect(url_for("room", code=code))


# ROOM

@app.route("/room/<code>")
def room(code):
    member_id = current_member(code)

    if not member_id:
        return redirect(url_for("home"))

    movies = fetch_movies()

    with get_db() as db:
        voted = db.execute(
            """SELECT movie_id FROM votes
               WHERE member_id = ? AND room_code = ?""",
            (member_id, code)
        ).fetchall()

    voted_ids = {row["movie_id"] for row in voted}

    movies = [
        movie for movie in movies
        if movie["id"] not in voted_ids
    ]

    return render_template(
        "room.html",
        code=code,
        movies=movies,
        api_configured=bool(TMDB_TOKEN)
    )


# VOTE

@app.route("/vote/<code>", methods=["POST"])
def vote(code):
    member_id = current_member(code)

    if not member_id:
        return jsonify({"error": "Not authorized"}), 403

    data = request.get_json(silent=True) or {}
    movie_id = data.get("movie_id")
    liked = data.get("liked")

    if type(movie_id) is not int or type(liked) is not bool:
        return jsonify({"error": "Invalid vote"}), 400

    with get_db() as db:
        db.execute(
            """INSERT OR REPLACE INTO votes
               (member_id, room_code, movie_id, liked)
               VALUES (?, ?, ?, ?)""",
            (member_id, code, movie_id, int(liked))
        )

    return jsonify({"success": True})


# MATCHES

@app.route("/matches/<code>")
def matches(code):
    if not current_member(code):
        return redirect(url_for("home"))

    with get_db() as db:
        rows = db.execute(
            """SELECT movie_id
               FROM votes
               WHERE room_code = ? AND liked = 1
               GROUP BY movie_id
               HAVING COUNT(DISTINCT member_id) = 2""",
            (code,)
        ).fetchall()

    match_ids = {row["movie_id"] for row in rows}
    movies = fetch_movies()

    matched_movies = [
        movie for movie in movies
        if movie["id"] in match_ids
    ]

    return render_template(
        "matches.html",
        code=code,
        movies=matched_movies
    )


if __name__ == "__main__":
    app.run(debug=True)
