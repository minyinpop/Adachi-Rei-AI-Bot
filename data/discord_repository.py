import sqlite3

from data.database_configs import DATABASE_PATH

def init_short_memory():
    with sqlite3.connect(DATABASE_PATH) as connect:
        cursor = connect.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS short_memory
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP,
                ai_provider TEXT,
                channel_id INTEGER,
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
        channel_id: int,
        user_id: int,
        user_name: str,
        user_role: str,
        user_message_type: str,
        user_message: str):
    with sqlite3.connect(DATABASE_PATH) as connect:
        cursor = connect.cursor()

        cursor.execute(
            """
            INSERT INTO short_memory
                (
                    created_at,
                    ai_provider,
                    channel_id,
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
                channel_id,
                user_id,
                user_name,
                user_role,
                user_message_type,
                user_message
            )
        )

def get_short_memory(channel_id: int, limit: int):
    with sqlite3.connect(DATABASE_PATH) as connect:
        cursor = connect.cursor()

        cursor.execute(
            """
            SELECT *
            FROM short_memory
            WHERE channel_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (
                channel_id,
                limit
            )
        )

        rows = cursor.fetchall()
        rows.reverse()

        return rows

def delete_short_memory(channel_id: int):
    with sqlite3.connect(DATABASE_PATH) as connect:
        cursor = connect.cursor()

        cursor.execute(
            """
            DELETE FROM short_memory
                WHERE channel_id = ?
            """,
            (
                channel_id,
            )
        )

        connect.commit()