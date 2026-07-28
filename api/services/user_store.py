"""M8: multi-user data model.

SQLite stands in for Supabase's Postgres here — zero setup, no account
needed. The swap-in point for real Supabase later is narrow: replace this
file's storage with Postgres rows, and replace the dev-only X-User header in
api/main.py with real JWT verification from Supabase Auth. Everything that
depends on "a user owns some repos" (the isolation logic in main.py) doesn't
change.
"""
import sqlite3
import uuid
from pathlib import Path


class UserStore:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL
            )"""
        )
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS repos (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id),
                name TEXT NOT NULL,
                git_url TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT
            )"""
        )
        self.conn.commit()

    def get_or_create_user(self, username: str) -> str:
        row = self.conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if row:
            return row[0]
        user_id = str(uuid.uuid4())
        self.conn.execute("INSERT INTO users (id, username) VALUES (?, ?)", (user_id, username))
        self.conn.commit()
        return user_id

    def create_repo(self, user_id: str, name: str, git_url: str) -> str:
        repo_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO repos (id, user_id, name, git_url, status) VALUES (?, ?, ?, ?, 'pending')",
            (repo_id, user_id, name, git_url),
        )
        self.conn.commit()
        return repo_id

    def set_repo_status(self, repo_id: str, status: str, error: str | None = None) -> None:
        self.conn.execute("UPDATE repos SET status=?, error=? WHERE id=?", (status, error, repo_id))
        self.conn.commit()

    def list_repos(self, user_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, name, git_url, status, error FROM repos WHERE user_id=? ORDER BY rowid DESC", (user_id,)
        ).fetchall()
        return [{"id": r[0], "name": r[1], "git_url": r[2], "status": r[3], "error": r[4]} for r in rows]

    def all_repos(self) -> list[dict]:
        """Across all users — used by the git webhook, which reacts to a
        push on a given URL regardless of which users are tracking it."""
        rows = self.conn.execute("SELECT id, user_id, git_url FROM repos").fetchall()
        return [{"id": r[0], "user_id": r[1], "git_url": r[2]} for r in rows]

    def get_repo(self, repo_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT id, user_id, name, git_url, status, error FROM repos WHERE id=?", (repo_id,)
        ).fetchone()
        if not row:
            return None
        return {"id": row[0], "user_id": row[1], "name": row[2], "git_url": row[3], "status": row[4], "error": row[5]}
