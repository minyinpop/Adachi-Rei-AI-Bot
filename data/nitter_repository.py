import sqlite3

from data.database_configs import DATABASE_PATH

def init_nitter_post():
    with sqlite3.connect(DATABASE_PATH) as connect:
        cursor = connect.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS nitter_post
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP,
                post_id INTEGER,
                post_url TEXT
            )
            """
        )

def insert_nitter_post(
        post_id: int,
        post_url: str):
    with sqlite3.connect(DATABASE_PATH) as connect:
        cursor = connect.cursor()

        cursor.execute(
            """
            INSERT INTO nitter_post
                (
                    created_at,
                    post_id,
                    post_url
                )
            VALUES
                (
                    CURRENT_TIMESTAMP, ?, ?
                )
            """,
            (
                post_id,
                post_url
            )
        )

def check_post_exists(post_id: int):
    with sqlite3.connect(DATABASE_PATH) as connect:
        cursor = connect.cursor()

        cursor.execute(
            """
            SELECT 1
            FROM nitter_post
            WHERE post_id = ?
            """,
            (
                post_id,
            )
        )

        return cursor.fetchone() is not None