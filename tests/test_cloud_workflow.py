import io,json,sqlite3,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import promo_workflow as promo
import skales_bridge as bridge
from queue_sync import connection

class VoiceConfig:
    @classmethod
    def from_env(cls,role='zoya'):return cls()
class FakeVoice:
    def __init__(self,*args):pass
    def plan(self,text,name):return {'hash':'approved-hash','payload':{'body':{'text':text}}}

class Tests(unittest.TestCase):
    def fixture(self):
        module=types.ModuleType('natural_voice');module.VoiceConfig=VoiceConfig;module.ElevenLabsVoice=FakeVoice
        return patch.dict(sys.modules,{'natural_voice':module})

    def test_duplicate_promo_does_not_deadlock_or_create_second_job(self):
        with tempfile.TemporaryDirectory() as directory,self.fixture():
            root=Path(directory)
            with patch.object(promo,'ROOT',root),patch.object(promo,'DB',root/'data/jobs.sqlite3'):
                a=promo.create('approved generic promo','same-id');b=promo.create('approved generic promo','same-id')
                self.assertEqual(a['id'],b['id']);self.assertEqual(len(promo.list_jobs()),1)
                with self.assertRaises(ValueError):promo.create('changed command','same-id')

    def test_voice_and_render_have_separate_approval_states(self):
        with tempfile.TemporaryDirectory() as directory,self.fixture():
            root=Path(directory)
            with patch.object(promo,'ROOT',root),patch.object(promo,'DB',root/'data/jobs.sqlite3'):
                job=promo.create('promo','request')
                self.assertEqual(job['status'],'awaiting_voice_approval')
                with self.assertRaises(ValueError):promo.approve_voice(job['id'],'changed-hash')
                with self.assertRaises(ValueError):promo.approve_render(job['id'],'unapproved-render')
                self.assertEqual(promo.get(job['id'])['status'],'awaiting_voice_approval')

    def test_quota_failure_persists_reason_and_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory,self.fixture():
            root=Path(directory)
            with patch.object(promo,'ROOT',root),patch.object(promo,'DB',root/'data/jobs.sqlite3'):
                job=promo.create('promo','request')
                failed=types.SimpleNamespace(returncode=1,stdout='',stderr='ValueError: Provider quota insufficient; no TTS sent')
                with patch.object(promo.subprocess,'run',return_value=failed) as transport:
                    with self.assertRaises(ValueError):promo.approve_voice(job['id'],'approved-hash')
                    with self.assertRaises(ValueError):promo.approve_voice(job['id'],'approved-hash')
                    self.assertEqual(transport.call_count,1)
                result=promo.get(job['id']);self.assertEqual(result['status'],'needs_review')
                self.assertEqual(json.loads(result['result'])['reason'],'provider_quota_insufficient')

    def test_default_external_task_is_blocked_without_verified_tool_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'data').mkdir()
            with patch.object(bridge,'ROOT',root),patch.object(bridge,'request') as transport:
                result=bridge.dispatch('publish content')
                self.assertEqual(result['status'],'needs_review')
                self.assertEqual(result['reason'],'external_tool_scope_unverified')
                transport.assert_not_called()

if __name__=='__main__':unittest.main()
