import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import install_hardening as installer
from voice_pipeline_hardening import build_tts_payload, normalize_stt, voice_cache_key
from skales_event_validation import classify_events
from queue_sync import initialize, claim, connection, finish

class Tests(unittest.TestCase):
    def test_exact_settings_and_punctuation(self):
        payload=build_tts_payload('Ji boss main sun rahi hoon')
        self.assertEqual(payload,{'text':'Ji boss, main sun rahi hoon.',
          'model_id':'eleven_multilingual_v2','voice_settings':{
           'stability':0.35,'similarity_boost':0.85,'style':0.20,'use_speaker_boost':True}})

    def test_transcript_cleanup_preserves_confirmations_and_names(self):
        for raw,expected in [('  haan\n','haan'),('/task  get_system_info','/task get_system_info'),('  Armaan\t Ismat  ','Armaan Ismat'),('हां','हां')]:
            self.assertEqual(normalize_stt(raw),expected)

    def test_no_semantic_or_decimal_guess(self):
        self.assertEqual(build_tts_payload('Price 13.99 USD')['text'],'Price 13.99 USD.')
        self.assertEqual(normalize_stt('unknown misheard word'),'unknown misheard word')

    def test_cache_includes_settings_and_model(self):
        import voice_pipeline_hardening as v
        base=voice_cache_key('voice-a','hello')
        self.assertNotEqual(base,voice_cache_key('voice-b','hello'))
        with patch.dict(v.VOICE_SETTINGS,{'stability':0.9}):self.assertNotEqual(base,voice_cache_key('voice-a','hello'))
        with patch.object(v,'MODEL_ID','eleven_turbo_v2_5'):self.assertNotEqual(base,voice_cache_key('voice-a','hello'))

    def test_text_done_never_passes_as_tool_execution(self):
        self.assertEqual(classify_events([{'type':'text','content':'done'},{'type':'done'}])['status'],'needs_review')
        self.assertFalse(classify_events([{'type':'tool_result','tool':'x','result':'ok'},{'type':'done'}])['tool_execution_observed'])

    def test_matching_tool_events_and_errors(self):
        events=[{'type':'tool_call','tool':'get_system_info','args':{}},{'type':'tool_result','tool':'get_system_info','result':'Windows'},{'type':'done'}]
        self.assertTrue(classify_events(events,['get_system_info'])['tool_execution_observed'])
        self.assertFalse(classify_events(events,['other'])['tool_execution_observed'])
        self.assertFalse(classify_events(events+[{'type':'tool_error','tool':'get_system_info'}])['tool_execution_observed'])
        self.assertFalse(classify_events(events+[{'type':'text','content':'This answer was produced without tools'}])['tool_execution_observed'])

    def test_threaded_claim_is_at_most_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'queue.sqlite3';initialize(path)
            with connection(path) as db:db.execute("INSERT INTO queue VALUES('one','command','queued',0)")
            barrier=threading.Barrier(2);rows=[];errors=[]
            def worker():
                try:barrier.wait();rows.append(claim(path))
                except Exception as e:errors.append(e)
            threads=[threading.Thread(target=worker) for _ in range(2)]
            for t in threads:t.start()
            for t in threads:t.join(15)
            self.assertTrue(all(not t.is_alive() for t in threads));self.assertEqual(errors,[])
            self.assertEqual(sum(row is not None for row in rows),1)
            finish(path,'one',{'status':'needs_review','reason':'no_tool_execution'})
            with connection(path) as db:
                self.assertEqual(db.execute('SELECT status FROM queue').fetchone()[0],'needs_review')
                self.assertEqual(json.loads(db.execute('SELECT result FROM queue_results').fetchone()[0])['reason'],'no_tool_execution')

    def test_restart_does_not_resubmit_unknown_outcome(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'q.db';initialize(path)
            with connection(path) as db:db.execute("INSERT INTO queue VALUES('one','unknown','running',0)")
            initialize(path);self.assertIsNone(claim(path))
            with connection(path) as db:self.assertEqual(db.execute('SELECT status FROM queue').fetchone()[0],'needs_review')

    def test_cancel_is_not_overwritten_by_late_result(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'q.db';initialize(path)
            with connection(path) as db:db.execute("INSERT INTO queue VALUES('one','command','cancelled',0)")
            finish(path,'one',{'status':'tool_result_received'})
            with connection(path) as db:self.assertEqual(db.execute('SELECT status FROM queue').fetchone()[0],'cancelled')

    def test_transport_payload_and_hash(self):
        source=(Path(__file__).parent/'fixtures/natural_voice.py').read_text()
        ns={'__name__':'fixture_voice'}
        # dataclass requires the module to be registered.
        import types,sys
        module=types.ModuleType('fixture_voice');sys.modules['fixture_voice']=module
        exec(compile(installer.patch_voice(source),'fixture','exec'),module.__dict__)
        class Response(io.BytesIO):
            status=200
            def getheader(self,name,default=''):return 'audio/mpeg'
        sent=[]
        class FakeConnection:
            def __init__(self,*args,**kw):pass
            def request(self,method,path,body,headers):sent.append(json.loads(body))
            def getresponse(self):return Response(b'ID3fixture-audio')
            def close(self):pass
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'ELEVENLABS_API_KEY':'fixture-key','ZOYA_VOICE_ID':'fixture-voice','ZOYA_VOICE_MODEL_ID':'old-model'}):
            config=module.VoiceConfig.from_env()
            self.assertEqual(config.model_id,'eleven_multilingual_v2')
            voice=module.ElevenLabsVoice(directory,config,FakeConnection)
            plan=voice.plan('Ji boss hello','sample.mp3')
            with self.assertRaises(module.VoiceError):voice.generate('Ji boss hello','sample.mp3','wrong-hash')
            self.assertEqual(sent,[])
            voice.generate('Ji boss hello','sample.mp3',plan['hash'])
            self.assertEqual(sent[0],build_tts_payload('Ji boss hello'))

    def test_installer_is_all_validated_before_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ('natural_voice.py','zoya_desktop.py','skales_bridge.py'):
                (root/name).write_text((Path(__file__).parent/'fixtures'/name).read_text())
            changes=installer.transform(root)
            self.assertIn('normalize_stt',changes['zoya_desktop.py'])
            self.assertIn('required_tools',changes['skales_bridge.py'])
            self.assertIn('queue_sync',changes['zoya_desktop.py'])
            # Changed live source must stop installation, not overwrite it from an older snapshot.
            (root/'natural_voice.py').write_text('class Unknown: pass')
            before=(root/'zoya_desktop.py').read_bytes()
            with self.assertRaises((StopIteration,ValueError)):installer.transform(root)
            self.assertEqual((root/'zoya_desktop.py').read_bytes(),before)

    def test_bridge_registry_and_actual_sse(self):
        import types
        source=(Path(__file__).parent/'fixtures/skales_bridge.py').read_text()
        module=types.ModuleType('fixture_bridge')
        module.__file__=str(Path(__file__).parent/'fixtures/skales_bridge.py')
        exec(compile(installer.patch_bridge(source),'bridge','exec'),module.__dict__)
        for enabled,expected in [(False,'needs_review'),(True,'tool_result_received')]:
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);(root/'data').mkdir();calls=[]
                def request(path,body=None,method=None):
                    calls.append(path)
                    if path.endswith('/status'):return io.BytesIO(b'{"provider":"ollama"}')
                    if path.endswith('/tools'):return io.BytesIO(json.dumps({'tools':[{'name':'get_system_info','enabled':enabled}]}).encode())
                    events=[{'type':'tool_call','tool':'get_system_info','args':{}},{'type':'tool_result','tool':'get_system_info','result':'Windows'},{'type':'done'}]
                    return io.BytesIO(''.join('data: '+json.dumps(e)+'\n\n' for e in events).encode())
                with patch.object(module,'ROOT',root),patch.object(module,'request',request):result=module.dispatch('test',required_tools=('get_system_info',))
                self.assertEqual(result['status'],expected)
                self.assertEqual(calls.count('/api/cli/chat'),int(enabled))

    def test_existing_budget_guards_and_scoped_signature_survive(self):
        source=(Path(__file__).parent/'fixtures/natural_voice.py').read_text()
        source=source.replace('def generate(self, text, output_name, approved_hash):','def generate(self, text, output_name, approved_hash, conversation_scope=False):')
        source=source.replace('        plan = self.plan(text, output_name)','        if os.environ.get("ZOYA_FREE_MODE")=="1" and not conversation_scope:\n            raise VoiceError("Budget guard retained")\n        plan = self.plan(text, output_name)')
        updated=installer.patch_voice(source)
        self.assertIn('conversation_scope=False',updated)
        self.assertIn('Budget guard retained',updated)

if __name__=='__main__':unittest.main()
