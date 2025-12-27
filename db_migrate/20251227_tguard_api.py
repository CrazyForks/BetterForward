import sqlite3


def upgrade(db_path):
    with sqlite3.connect(db_path) as conn:
        db_cursor = conn.cursor()
        # Add TGuard API settings
        db_cursor.execute("""
            INSERT OR IGNORE INTO settings (key, value) VALUES ('tguard_api_url', NULL)
        """)
        db_cursor.execute("""
            INSERT OR IGNORE INTO settings (key, value) VALUES ('tguard_api_key', NULL)
        """)
        conn.commit()

