import sqlite3
DB="trademind.db"
def init_db():
    with sqlite3.connect(DB) as c:
        c.execute("CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY,created_at TEXT,symbol TEXT,direction TEXT,entry REAL,sl REAL,tp REAL,rr REAL DEFAULT 2,result TEXT,pl_r REAL,score INTEGER,notes TEXT)")
def list_trades(limit=10):
    with sqlite3.connect(DB) as c:
        c.row_factory=sqlite3.Row
        return [dict(x) for x in c.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?",(limit,)).fetchall()]
