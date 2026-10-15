"""Scoped Windows installer for the inspected Zoya v0.7 Desktop implementation.

No model download, provider spending, login, upload or publishing. Source
patterns are checked before modifying app/desktop. Rollback checkpoint is saved.
"""
import argparse
import contextlib
import hashlib
import json
import os
import py_compile
import shutil
import sqlite3
import subprocess
import sys
import time
import venv
from pathlib import Path

RUNTIME=['video_engine.py','voice_listener.py','service_integration.py',
         'prepare_models.py','realtime_desktop.py','test_upgrade.py']


def once(s,old,new):
    if s.count(old)!=1:
        raise ValueError('Source layout changed; review before patching: '+old[:70])
    return s.replace(old,new,1)


def replace_method(s,name,next_name,body):
    a=s.index(' def '+name+'(');b=s.index(' def '+next_name+'(',a+1)
    return s[:a]+body+'\n'+s[b:]


def patch_desktop(s):
    if '# ZOYA_REALTIME_LIVE_V1' in s:return s
    s=once(s,'ROOT=Path(__file__).resolve().parent', '''ROOT=Path(__file__).resolve().parent
# ZOYA_REALTIME_LIVE_V1
if __name__=='__main__' and (ROOT/'data/realtime-enabled.json').exists():
 target=ROOT/'tools/realtime-env/Scripts/pythonw.exe'
 if not target.is_file():raise SystemExit('Realtime environment missing; use the saved rollback checkpoint.')
 if Path(sys.executable).resolve().parent!=target.parent.resolve():
  subprocess.Popen([str(target),str(Path(__file__).resolve()),*sys.argv[1:]],cwd=ROOT)
  raise SystemExit(0)''')
    s=once(s,'  setup(self)\n','  setup(self)\n  from realtime_desktop import initialize\n  initialize(self)\n')
    s=once(s,' def close(self):self.closed.set();self.root.destroy()',
           ' def close(self):\n  self.closed.set()\n  from realtime_desktop import close\n  close(self)\n  self.root.destroy()')
    s=replace_method(s,'listen','submit',' def listen(self):\n  from realtime_desktop import run_listener\n  return run_listener(self)')
    s=once(s,' def reply(self,text):\n',' def reply(self,text):\n  from realtime_desktop import begin_turn\n  begin_turn(self)\n')
    s=once(s,"   self.post('Zoya',result['reply']);self.post('status','Jawab ready · voice taiyar ho rahi hai')",
           "   from realtime_desktop import valid_turn\n   if not valid_turn(self):return\n   self.post('Zoya',result['reply']);self.post('status','Jawab ready · voice taiyar ho rahi hai')")
    s=once(s,' def say(self,text):\n',' def say(self,text):\n  from realtime_desktop import valid_turn\n  if not valid_turn(self):return\n')
    s=replace_method(s,'play_mp3','local_say',' def play_mp3(self,path):\n  from realtime_desktop import play\n  return play(self,path)')
    s=once(s,"  winsound.PlaySound(str(path),winsound.SND_FILENAME)",'  self.play_mp3(path)')
    compile(s,'zoya_desktop.py','exec')
    return s


def patch_app(s):
    if '# ZOYA_SYNCED_4K_LIVE_V1' in s:return s
    s=once(s,'    studio=Studio(ROOT)\n', '''    studio=Studio(ROOT)
    # ZOYA_SYNCED_4K_LIVE_V1
    from service_integration import RenderQueue
    render_queue=RenderQueue(ROOT/'workspaces/Synced4K')
    render_queue.start()
''')
    needle="                elif path=='/api/studio/create':"
    s=once(s,needle,"                elif path=='/api/video-engine/create':\n                    result=render_queue.create(p.get('manifest'),p.get('request_id'))\n                elif path=='/api/video-engine/status':\n                    result=render_queue.get(p.get('id'))\n"+needle)
    s=once(s,'        shutdown.set()\n        server.server_close()',
           '        shutdown.set()\n        render_queue.close()\n        server.server_close()')
    compile(s,'app.py','exec')
    return s


def configure_tools():
    pointer=Path(os.environ['LOCALAPPDATA'])/'ZoyaAgent/tools/ffmpeg-bin.txt'
    if pointer.is_file():
        folder=Path(pointer.read_text(encoding='utf-8-sig').strip())
        if (folder/'ffmpeg.exe').is_file() and (folder/'ffprobe.exe').is_file():
            os.environ['PATH']=str(folder)+os.pathsep+os.environ.get('PATH','')
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise ValueError('Existing FFmpeg/ffprobe not found')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',required=True,type=Path)
    p.add_argument('--apply',action='store_true')
    args=p.parse_args();root=args.root.resolve();source=Path(__file__).resolve().parent
    if sys.platform!='win32':raise SystemExit('Run on the authorized Windows laptop')
    if not (root/'zoya_desktop.py').is_file() or not (root/'app.py').is_file():
        raise SystemExit('Existing Zoya installation not found')
    if not (root/'data/offline/faster-whisper-base/model.bin').is_file():
        raise SystemExit('Existing local speech model missing; no implicit download')
    if not (root/'data/zoya.sqlite3').is_file():
        raise SystemExit('Existing Zoya database missing; no empty replacement is created')
    desktop=patch_desktop((root/'zoya_desktop.py').read_text(encoding='utf-8'))
    app=patch_app((root/'app.py').read_text(encoding='utf-8'))
    configure_tools()
    if not args.apply:
        print('Source patterns verified; --apply installs only inside '+str(root));return
    # OS lock is released on process exit, including an interrupted installer.
    import msvcrt
    lock=(root/'data/realtime-installer.lock').open('a+b')
    lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
    try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:raise SystemExit('Another installer is running; do not duplicate it')
    cp=root/'data'/('realtime-live-checkpoint-'+time.strftime('%Y%m%d-%H%M%S'))
    cp.mkdir(parents=True)
    names=['app.py','zoya_desktop.py',*RUNTIME,'data/realtime-enabled.json']
    for name in names:
        f=root/name
        if f.is_file():
            dst=cp/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(f,dst)
    src=sqlite3.connect(root/'data/zoya.sqlite3',timeout=15)
    dst=sqlite3.connect(cp/'zoya.sqlite3');src.backup(dst);dst.close();src.close()
    report={'checkpoint':str(cp),'stage':'dependencies','runtime_verified':False}
    report_path=root/'data/realtime-live-install.json'
    def record():report_path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    record()
    env=root/'tools/realtime-env'
    if not (env/'Scripts/python.exe').exists():
        venv.EnvBuilder(with_pip=True,system_site_packages=True).create(env)
    python=env/'Scripts/python.exe'
    req=root/'requirements-realtime.txt' if source==root else source/'requirements.txt'
    if not req.is_file():raise ValueError('Reviewed realtime requirements file missing')
    with (root/'data/realtime-dependency-install.log').open('w',encoding='utf-8') as log:
        subprocess.run([str(python),'-m','pip','install','--only-binary=:all:','-r',str(req)],
                       stdout=log,stderr=subprocess.STDOUT,cwd=root,check=True,timeout=600)
    # Copy reviewed modules before changing live entrypoints.
    for name in RUNTIME:
        if source/name != root/name:shutil.copy2(source/name,root/name)
    os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
    report['stage']='local_models';record()
    with (root/'data/realtime-model-check.log').open('w',encoding='utf-8') as log:
        subprocess.run([str(python),str(root/'prepare_models.py'),'--vad-output',str(root/'models/silero_vad_v6.onnx'),
                        '--whisper-source',str(root/'data/offline/faster-whisper-base')],
                       stdout=log,stderr=subprocess.STDOUT,cwd=root,check=True,timeout=120)
    report['stage']='tests';record()
    test_env=os.environ.copy();test_env['ZOYA_TEST_4K']='1'
    with (root/'data/realtime-windows-tests.log').open('w',encoding='utf-8') as log:
        subprocess.run([str(python),'-m','unittest','test_upgrade','-v'],cwd=root,env=test_env,
                       stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300)
    report['stage']='applying_entrypoints';record()
    try:
        (root/'zoya_desktop.py').write_text(desktop,encoding='utf-8')
        (root/'app.py').write_text(app,encoding='utf-8')
        for name in ['zoya_desktop.py','app.py',*RUNTIME]:
            py_compile.compile(str(root/name),doraise=True)
        (root/'data/realtime-enabled.json').write_text(json.dumps({'enabled':True,'checkpoint':str(cp)}))
    except Exception:
        for name in ['app.py','zoya_desktop.py']:shutil.copy2(cp/name,root/name)
        (root/'data/realtime-enabled.json').unlink(missing_ok=True)
        report['stage']='entrypoint_rollback';record();raise
    report['stage']='restarting';record()
    script_path=source/'ready-script.json'
    if script_path.is_file():
        sys.path.insert(0,str(root))
        from production_studio import Studio
        script=json.loads(script_path.read_text(encoding='utf-8'))
        key=hashlib.sha256(json.dumps(script,sort_keys=True).encode()).hexdigest()[:12]
        job=Studio(root).create(script,'reviewed-script-'+key)
        import ctypes
        desktop_path=ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None,0x10,None,0,desktop_path)==0:
            folder=Path(desktop_path.value)/'Zoya Production - Ready Scripts';folder.mkdir(exist_ok=True)
            shutil.copy2(script_path,folder/'Armaan-Ismat-4K-Script.json')
            if (source/'ready-script.md').is_file():
                shutil.copy2(source/'ready-script.md',folder/'Armaan-Ismat-4K-Script.txt')
            report['desktop_script_folder']=str(folder)
        report['ready_script_job_id']=job['id'];record()
    subprocess.run([str(python),str(root/'restart_running_only.py')],cwd=root,check=True,timeout=30)
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        time.sleep(1)
        try:
            health=json.loads((root/'data/desktop-health.json').read_text(encoding='utf-8'))
            listener=health.get('realtime_listener',{})
            if health.get('heartbeat',0)>time.time()-8 and listener.get('state')=='listening':
                report.update(stage='installed_microphone_open',runtime_verified=True,
                              device_index=listener.get('device_index'),sample_rate=listener.get('sample_rate'),
                              physical_speech_test='pending',physical_barge_in_test='pending')
                record();print(json.dumps(report,indent=2));return
        except (OSError,ValueError):pass
    report['stage']='installed_needs_live_review';record()
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
