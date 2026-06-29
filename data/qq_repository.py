import sqlite3

from data.database_configs import DATABASE_PATH

def init_short_memory():
    with sqlite3.connect(DATABASE_PATH) as connect:
        cursor = connect.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS qq_short_memory
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP,
                ai_provider TEXT,
                target_id INTEGER,
                user_id INTEGER,
                user_name TEXT,
                user_role TEXT,
                user_message_type TEXT,
                user_message TEXT
            )
            """
        )

def insert_short_memory(
        ai_provider: str,
        target_id: int,
        user_id: int,
        user_name: str,
        user_role: str,
        user_message_type: str,
        user_message: str):
    with sqlite3.connect(DATABASE_PATH) as connect:
        cursor = connect.cursor()

        cursor.execute(
            """
            INSERT INTO qq_short_memory
                (
                    created_at,
                    ai_provider,
                    target_id,
                    user_id,
                    user_name,
                    user_role,
                    user_message_type,
                    user_message
                )
            VALUES
                (
                    CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?
                )
            """,
            (
                ai_provider,
                target_id,
                user_id,
                user_name,
                user_role,
                user_message_type,
                user_message
            )
        )

def get_short_memory(target_id: int, limit: int):
    with sqlite3.connect(DATABASE_PATH) as connect:
        cursor = connect.cursor()

        cursor.execute(
            """
            SELECT *
            FROM qq_short_memory
            WHERE target_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (
                target_id,
                limit
            )
        )

        rows = cursor.fetchall()
        rows.reverse()

        return rows