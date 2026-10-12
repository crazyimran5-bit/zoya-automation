"""Durable queue claims. Connections belong to one thread and are always closed."""
import json
import sqlite3
from contextlib import contextmanager

@contextmanager
def connection(path):
    db = sqlite3.connect(path, timeout=10)
    try:
        db.execute('PRAGMA busy_timeout=10000')
        with db:
            yield db
    finally:
        db.close()

def initialize(path):
    with connection(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS queue(id TEXT PRIMARY KEY,message TEXT,status TEXT,created REAL)')
        db.execute('CREATE TABLE IF NOT EXISTS queue_results(id TEXT PRIMARY KEY,result TEXT)')
        # Called once by the mutex-protected native desktop at restart, not each poll.
        db.execute("UPDATE queue SET status='needs_review' WHERE status='running'")

def claim(path):
    with connection(path) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT id,message FROM queue WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
        if row:
            db.execute("UPDATE queue SET status='running' WHERE id=? AND status='queued'", (row[0],))
        return row

def finish(path, task_id, result):
    status = result.get('status', 'needs_review')
    with connection(path) as db:
        # Persist evidence and queue status in the same transaction; respect cancellation.
        db.execute('INSERT OR REPLACE INTO queue_results VALUES(?,?)',
                   (task_id, json.dumps(result, ensure_ascii=False)))
        db.execute("UPDATE queue SET status=? WHERE id=? AND status='running'", (status, task_id))
