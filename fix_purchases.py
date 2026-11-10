import sqlite3
import os

db_path = os.path.join("database", "retail.db")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

try:
    cursor.execute("ALTER TABLE purchases ADD COLUMN source TEXT;")
    print("✅ Added 'source' column to purchases table!")
except sqlite3.OperationalError as e:
    if "duplicate column name" in str(e):
        print("⚠️ 'source' column already exists.")
    else:
        print(f"❌ Error: {e}")

conn.commit()
conn.close()