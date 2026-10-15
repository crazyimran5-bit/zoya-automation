"""Hooks for Zoya's existing authenticated port-8767 service and Tk desktop.

No HTTP server, microphone or provider request starts on import.
"""
from __future__ import annotations

import hashlib
import json
import queue
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from video_engine import render_video, _local, _sha


class RenderQueue:
    """One local encoder worker; durable requests/results; no automatic replay.

    Call handle() only AFTER the service's localhost, Origin and token checks.
    Input assets/output are confined to the assigned workspace.
    """
    def __init__(self, workspace: str | Path):
        self.root = Path(workspace).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root/'video-engine-jobs.sqlite3'
        self.wake = threading.Event()
        self.stop_event = threading.Event()
        self.thread = None
        with self._db() as c:
            c.execute('CREATE TABLE IF NOT EXISTS renders(id TEXT PRIMARY KEY, request_id TEXT UNIQUE, digest TEXT, manifest TEXT, status TEXT, result TEXT, updated REAL)')
            c.execute("UPDATE renders SET status='interrupted',result='Worker restarted; inspect output and .lock before a fresh render request.' WHERE status='running'")

    def _db(self):
        # Connections used in context managers are also explicitly closed below.
        from contextlib import contextmanager
        @contextmanager
        def connection():
            c = sqlite3.connect(self.database, timeout=10)
            c.row_factory = sqlite3.Row
            try:
                with c: yield c
            finally: c.close()
        return connection()

    def create(self, manifest: dict, request_id: str):
        allowed = {'script_text', 'audio_path', 'timestamps', 'media_assets', 'output_path',
                   'fps', 'codec', 'require_native_4k', 'burn_subtitles', 'crf', 'threads'}
        required = {'script_text', 'audio_path', 'timestamps', 'media_assets', 'output_path'}
        if not isinstance(manifest, dict) or set(manifest)-allowed or not required <= set(manifest):
            raise ValueError('Invalid render manifest fields')
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
            raise ValueError('Stable request_id required')
        # Render service accepts stored timestamps. Model extraction can be done
        # upstream via extract_timestamps; request bodies cannot choose a model path.
        if manifest['timestamps'] is None:
            raise ValueError('Render service needs stored alignment timestamps')
        assets = manifest['media_assets']
        if not isinstance(assets,list) or not 1 <= len(assets) <= 100 or any(not isinstance(a,dict) or not isinstance(a.get('path'),str) for a in assets):
            raise ValueError('Supply 1-100 local media asset objects')
        paths = [manifest['audio_path']] + [a['path'] for a in assets]
        if any(not isinstance(p,str) for p in paths):
            raise ValueError('Input paths must be strings')
        _local(manifest['output_path'],self.root)
        hashes = {p:_sha(_local(p,self.root)) for p in paths}
        payload = json.dumps({'manifest':manifest,'inputs':hashes}, sort_keys=True, allow_nan=False)
        if len(payload) > 1_000_000:
            raise ValueError('Render manifest too large')
        fingerprint = hashlib.sha256(payload.encode()).hexdigest()
        with self._db() as c:
            c.execute('BEGIN IMMEDIATE')
            old = c.execute('SELECT * FROM renders WHERE request_id=?', (request_id,)).fetchone()
            if old:
                if old['digest'] != fingerprint: raise ValueError('Request ID reused with changed media plan')
                return self._public(old)
            if c.execute("SELECT COUNT(*) FROM renders WHERE status IN ('queued','running')").fetchone()[0] >= 4:
                raise ValueError('Render queue full')
            jid = str(uuid.uuid4())
            c.execute('INSERT INTO renders VALUES(?,?,?,?,?,?,?)',
                      (jid, request_id, fingerprint, payload, 'queued', '', time.time()))
        self.wake.set()
        return self.get(jid)

    @staticmethod
    def _public(row):
        d = dict(row)
        d.pop('manifest', None)
        if d['status'] == 'complete': d['result'] = json.loads(d['result'])
        return d

    def get(self, jid):
        with self._db() as c:
            row = c.execute('SELECT * FROM renders WHERE id=?', (jid,)).fetchone()
        if row is None: raise ValueError('Unknown render job')
        return self._public(row)

    def start(self):
        if self.thread: raise RuntimeError('Render worker already started')
        self.thread = threading.Thread(target=self._worker, daemon=True, name='zoya-4k-render')
        self.thread.start(); self.wake.set()

    def close(self):
        # Bounded subprocess continues in this process until completion; no unsafe
        # terminate halfway through writing a file. Service shutdown marks restart.
        self.stop_event.set(); self.wake.set()

    def _worker(self):
        while not self.stop_event.is_set():
            self.wake.wait(1); self.wake.clear()
            with self._db() as c:
                c.execute('BEGIN IMMEDIATE')
                row = c.execute("SELECT * FROM renders WHERE status='queued' ORDER BY updated LIMIT 1").fetchone()
                if not row: continue
                c.execute("UPDATE renders SET status='running',updated=? WHERE id=?", (time.time(), row['id']))
            try:
                saved=json.loads(row['manifest'])
                for p,h in saved['inputs'].items():
                    if _sha(_local(p,self.root)) != h:
                        raise ValueError('Queued input changed; prepare a new reviewed request')
                result = render_video(workspace=self.root, **saved['manifest'])
                state, body = 'complete', json.dumps(result)
            except Exception as exc:
                state, body = 'failed', type(exc).__name__+': '+str(exc)[:1500]
            with self._db() as c:
                c.execute('UPDATE renders SET status=?,result=?,updated=? WHERE id=?', (state, body, time.time(), row['id']))
            self.wake.set()


class DesktopVoiceAdapter:
    """Run the new listener inside the process which OWNS Zoya playback.

    dispatch_text is the SAME function used by typed commands. Calls are marshaled
    to the Tk main thread; no Tk call is made from an audio/STT callback.
    stop_playback is invoked from a dedicated fast interruption worker and must be
    thread-safe. Use InterruptiblePlayer.stop for this contract.
    """
    def __init__(self, tk_root, dispatch_text, stop_playback, config):
        from voice_listener import VoiceListener
        self.root, self.dispatch = tk_root, dispatch_text
        self.events = queue.Queue(maxsize=8)
        self.closed = False
        self.listener = VoiceListener(config, self._enqueue, stop_playback)
        self.after_id = self.root.after(20, self._pump)
        self.start_error = None

    def _enqueue(self, text, request_id):
        self.events.put_nowait((text, request_id))

    def _pump(self):
        if self.closed: return
        try:
            while True:
                text, request_id = self.events.get_nowait()
                self.dispatch(text, request_id)
        except queue.Empty:
            pass
        finally:
            if not self.closed: self.after_id = self.root.after(20, self._pump)

    def start(self):
        def load():
            try: self.listener.start()
            except Exception as exc:
                self.start_error = type(exc).__name__+': '+str(exc)
        threading.Thread(target=load, daemon=True, name='zoya-listener-start').start()

    def close(self):
        self.closed = True
        self.root.after_cancel(self.after_id)
        self.listener.stop()
