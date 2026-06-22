import sqlite3

from data.database_configs import DATABASE_PATH

def init_x_post():
    with sqlite3.connect(DATABASE_PATH) as connect:
        cursor = connect.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS x_post
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP,
                post_id INTEGER,
                post_url TEXT,
                post_title TEXT
            )
            """
        )

def insert_x_post(
        post_id: int,
        post_url: str,
        post_title: str):
    with sqlite3.connect(DATABASE_PATH) as connect:
        cursor = connect.cursor()

        cursor.execute(
            """
            INSERT INTO x_post
                (
                    created_at,
                    post_id,
                    post_url,
                    post_title
                )
            VALUES
                (
                    CURRENT_TIMESTAMP, ?, ?, ?
                )
            """,
            (
                post_id,
                post_url,
                post_title
            )
        )

def check_post_exists(post_id: int):
    with sqlite3.connect(DATABASE_PATH) as connect:
        cursor = connect.cursor()

        cursor.execute(
            """
            SELECT 1
            FROM x_post
            WHERE post_id = ?
            """,
            (
                post_id,
            )
        )

        return cursor.fetchone() is not None