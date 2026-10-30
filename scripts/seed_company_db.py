"""Create data/company.db, the SQLite "company memory" that Toolbox serves read-only.

    python scripts/seed_company_db.py            # create it if missing
    python scripts/seed_company_db.py --reset    # rebuild from scratch

Only this script writes the database; the agents can only run the three lookups in
toolbox/tools.yaml. `delivered` rows come from real AgentCorp runs, `sample` rows are
invented examples that make the lookups worth trying.
"""

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "company.db"

SCHEMA = """
CREATE TABLE projects (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    engagement_type TEXT NOT NULL,
    stack TEXT NOT NULL,
    outcome TEXT NOT NULL,
    tests_passed INTEGER NOT NULL,
    tests_total INTEGER NOT NULL,
    delivered_on TEXT NOT NULL,
    data_origin TEXT NOT NULL CHECK (data_origin IN ('delivered', 'sample'))
);
CREATE TABLE lessons (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    topic TEXT NOT NULL,
    lesson TEXT NOT NULL
);
"""

PROJECTS = [
    (1, "Email sign-up page",
     "Single page with one email box. A valid address is saved and the visitor sees a thank-you "
     "message; empty or invalid input shows a clear error; duplicates are not stored twice.",
     "web_app", "HTML + CSS + vanilla JS, FastAPI, SQLite",
     "Delivered after three defects found by running the app by hand.", 17, 17, "2026-10-30", "delivered"),
    (2, "Booking request form",
     "Public form where a customer asks for an appointment slot; staff see a list of requests.",
     "web_app", "HTML + vanilla JS, FastAPI, SQLite",
     "Delivered; staff list moved to a later phase.", 21, 21, "2026-08-14", "sample"),
    (3, "Todo list API",
     "JSON API to create, list, complete and delete todo items for one user.",
     "api_service", "FastAPI, SQLite",
     "Delivered; authentication left out of scope by the client.", 14, 14, "2026-07-02", "sample"),
]

LESSONS = [
    (1, "static files",
     "The HTML linked style.css and app.js but the server never served them, so the page looked "
     "broken. Serve the frontend folder from the app and use absolute /static/ URLs."),
    (1, "javascript",
     "app.js had a syntax error (a missing closing brace) that no Python test could see. Run "
     "a syntax check on every JavaScript file and open the page in a browser before sign-off."),
    (1, "database",
     "The emails table was created only by the test setup, so the real app returned 500 on the "
     "first sign-up. Create tables when the app starts, and smoke-test against a fresh database."),
    (1, "tests",
     "All 17 acceptance tests passed while the page was broken. Passing tests prove the "
     "requirements they cover, not that the product runs: always do one real run."),
    (2, "scope", "Clients asked for a staff dashboard late. Capture 'who sees the data' as a "
                 "question in discovery, not an afterthought."),
    (3, "authentication", "The client assumed logins. Ask 'is it one user or many?' before "
                          "designing anything with accounts."),
]


def build(path: Path = DB_PATH, reset: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not reset:
            return path
        path.unlink()
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO projects VALUES (?,?,?,?,?,?,?,?,?,?)", PROJECTS)
        conn.executemany("INSERT INTO lessons (project_id, topic, lesson) VALUES (?,?,?)", LESSONS)
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="delete and rebuild the database")
    args = parser.parse_args()
    print(f"company memory ready: {build(reset=args.reset)}")
