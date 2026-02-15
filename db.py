import sqlite3

class Database:
    def __init__(self, db_path):
        self.db_path = db_path
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.row_factory = sqlite3.Row

    def init_db(self):
        self.conn.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL DEFAULT 'New Chat',
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        self.conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            provider TEXT,
            model TEXT,
            FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
        )""")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id)")
        self.conn.commit()

    def create_chat(self, provider, model, title="New Chat"):
        cursor = self.conn.execute(
            "INSERT INTO chats (provider, model, title) VALUES (?, ?, ?)",
            (provider, model, title)
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_chats(self):
        return self.conn.execute("SELECT * FROM chats ORDER BY created_at DESC").fetchall()

    def get_messages(self, chat_id):
        return self.conn.execute(
            "SELECT role, content, provider, model FROM messages WHERE chat_id = ? ORDER BY id ASC",
            (chat_id,)
        ).fetchall()

    def add_message(self, chat_id, role, content, provider, model):
        self.conn.execute(
            "INSERT INTO messages (chat_id, role, content, provider, model) VALUES (?, ?, ?, ?, ?)",
            (chat_id, role, content, provider, model)
        )
        self.conn.commit()

    def update_chat_title(self, chat_id, title):
        self.conn.execute("UPDATE chats SET title = ? WHERE id = ?", (title, chat_id))
        self.conn.commit()

    def update_chat_model(self, chat_id, model, provider=None):
        if provider:
            self.conn.execute("UPDATE chats SET model = ?, provider = ? WHERE id = ?", (model, provider, chat_id))
        else:
            self.conn.execute("UPDATE chats SET model = ? WHERE id = ?", (model, chat_id))
        self.conn.commit()

    def delete_chat(self, chat_id):
        self.conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
        self.conn.commit()
