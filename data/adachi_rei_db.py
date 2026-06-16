import sqlite3

def init_short_memory():
    with sqlite3.connect("adachi_rei.db") as connect:
        cursor = connect.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS short_memory
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP,
                channel_id INTEGER,
                provider TEXT,
                role TEXT,
                content TEXT
            )
            """
        )

def insert_short_memory(channel_id: int, provider: str, role: str, content: str):
    with sqlite3.connect("adachi_rei.db") as connect:
        cursor = connect.cursor()

        cursor.execute(
            """
            INSERT INTO short_memory
                (
                    created_at,
                    channel_id,
                    provider,
                    role,
                    content
                )
            VALUES
                (
                    CURRENT_TIMESTAMP, ?, ?, ?, ?
                )
            """,
            (
                channel_id,
                provider,
                role,
                content
            )
        )

init_short_memory()

insert_short_memory(
    channel_id=338558312079687680,
    provider="openai",
    role="user",
    content="Hello, world!"
)