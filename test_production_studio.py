import unittest,tempfile,json
from pathlib import Path
from unittest.mock import patch
from production_studio import Studio,sha
SCRIPT={'title':'Play together','characters':[{'name':'Armaan','voice_description':'gentle young boy','appearance':'blue shirt'}],'scenes':[{'duration':1,'action':'Armaan waves and smiles','dialogue':[{'speaker':'Armaan','text':'Hello friend!'}]}]}
class WorkflowTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.s=Studio(self.t.name);self.j=self.s.create(SCRIPT,'test');self.jid=self.j['id']
 def tearDown(self):self.t.cleanup()
 def submitted(self):
  h=self.s.handoff(self.jid,0);self.s.record_submission(self.jid,0,'provider-123',h['request_key'])
 def accepted(self):
  self.submitted();p=self.s.base/'scene.mp4';p.write_bytes(b'fixture-only')
  receipt={'path':'scene.mp4','sha256':sha(p),'provider_job_id':'provider-123','method':'generated_dialogue','review':dict.fromkeys(['identity','action','lip_sync','matching_voice','natural_pacing'],True)}
  with patch('production_studio.probe_media',return_value={'duration':1,'streams':[{'codec_type':'audio'},{'codec_type':'video'}]}):self.s.accept_scene(self.jid,0,receipt)
  return receipt
 def test_idempotent_request(self):self.assertEqual(self.s.create(SCRIPT,'test')['id'],self.jid)
 def test_changed_request_blocked(self):
  s=json.loads(json.dumps(SCRIPT));s['title']='changed'
  with self.assertRaises(ValueError):self.s.create(s,'test')
 def test_no_duplicate_provider(self):
  self.submitted()
  with self.assertRaises(ValueError):self.s.record_submission(self.jid,0,'other',self.s.handoff(self.jid,0)['request_key'])
 def test_caption_before_motion_blocked(self):
  with self.assertRaises(ValueError):self.s.captions(self.jid,[{'start':0,'end':1,'text':'Hello friend!'}])
 def test_stock_receipt_blocked(self):
  receipt=self.accepted();receipt['method']='stock_overdub'
  with self.assertRaises(ValueError):self.s.accept_scene(self.jid,0,receipt)
 def test_unreviewed_lipsync_blocked(self):
  receipt=self.accepted();receipt['review']['lip_sync']=False
  with self.assertRaises(ValueError):self.s.accept_scene(self.jid,0,receipt)
 def test_caption_match(self):
  self.accepted()
  with self.assertRaises(ValueError):self.s.captions(self.jid,[{'start':0,'end':1,'text':'Wrong words'}])
  self.assertEqual(self.s.captions(self.jid,[{'start':0,'end':1,'text':'Hello friend!'}])['status'],'ready_to_render')
 def test_modified_media_blocked_on_render(self):
  self.accepted();self.s.captions(self.jid,[{'start':0,'end':1,'text':'Hello friend!'}]);(self.s.base/'scene.mp4').write_bytes(b'changed')
  with self.assertRaises(ValueError):self.s.render(self.jid)
  self.assertEqual(self.s.get(self.jid)['status'],'render_failed')
 def test_restart_recovery(self):
  with self.s.db() as c:
   j=self.s._get(c,self.jid);j['status']='rendering';self.s.save(c,j)
  new=Studio(self.t.name);self.assertEqual(new.get(self.jid)['status'],'render_interrupted')
 def test_no_fake_ready(self):
  with self.assertRaises(ValueError):self.s.review(self.jid,'fake',dict.fromkeys(['motion','lip_sync','voice','subtitle_sync','visual_continuity'],True))
 def test_path_escape(self):
  self.submitted()
  with self.assertRaises(ValueError):self.s.accept_scene(self.jid,0,{'path':'../escape.mp4'})
 def test_auto_captions_match_actual_words(self):
  self.accepted();m=self.s.root/'data/offline/faster-whisper-base';m.mkdir(parents=True);(m/'model.bin').write_bytes(b'fixture')
  from types import SimpleNamespace as N
  fake=N(transcribe=lambda *a,**k:([N(words=[N(word='Hello',start=0,end=.3),N(word='friend!',start=.3,end=.9)])],None))
  with patch.dict('sys.modules',{'whisper_backend':N(WhisperModel=lambda *a,**k:fake)}):
   self.assertEqual(self.s.auto_captions(self.jid)['status'],'ready_to_render')
 def test_auto_captions_mismatch_stops(self):
  self.accepted();m=self.s.root/'data/offline/faster-whisper-base';m.mkdir(parents=True);(m/'model.bin').write_bytes(b'fixture')
  from types import SimpleNamespace as N
  fake=N(transcribe=lambda *a,**k:([N(words=[N(word='wrong',start=0,end=.9)])],None))
  with patch.dict('sys.modules',{'whisper_backend':N(WhisperModel=lambda *a,**k:fake)}):
   with self.assertRaises(ValueError):self.s.auto_captions(self.jid)
  self.assertEqual(self.s.get(self.jid)['status'],'awaiting_captions')
 def test_no_generation_from_handoff(self):
  h=self.s.handoff(self.jid,0);self.assertFalse(h['submitted']);self.assertEqual(self.s.get(self.jid)['status'],'awaiting_performance')
if __name__=='__main__':unittest.main()
