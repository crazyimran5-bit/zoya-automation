import contextlib
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as N
from unittest.mock import patch

from video_engine import (Timing, alignment_to_timestamps, normalize_timestamps,
                          plan_scenes, render_video, _local)
from voice_listener import (ListenerConfig, SpeechSegmenter, InterruptiblePlayer,
                            VoiceListener, LocalAgentClient)
from service_integration import RenderQueue


class TimingTests(unittest.TestCase):
    def setUp(self):
        self.units = [Timing(.1, .4, 'Hello'), Timing(.6, .95, 'world!')]

    def test_dynamic_scene_boundaries_include_silence(self):
        p = plan_scenes(self.units, [{'path': 'a.mp4', 'text': 'Hello'},
                                    {'path': 'b.jpg', 'text': 'world!'}], 1.2, 30)
        self.assertEqual([(s.start_frame,s.end_frame) for s in p], [(0,18),(18,36)])

    def test_60fps_quantization(self):
        p = plan_scenes(self.units, [{'path': 'a.mp4', 'end_index': 2}], 1.201, 60)
        self.assertEqual(p[-1].end_frame, 73)

    def test_elevenlabs_character_alignment(self):
        text = 'Hi all'
        a = {'alignment': {'characters': list(text),
             'character_start_times_seconds':[i*.1 for i in range(len(text))],
             'character_end_times_seconds':[(i+1)*.1 for i in range(len(text))]}}
        words = alignment_to_timestamps(a)
        self.assertEqual([w['text'] for w in words], ['Hi','all'])
        self.assertAlmostEqual(words[1]['start'], .3)
        normalize_timestamps(text, a, 1)

    def test_bad_alignment_lengths(self):
        with self.assertRaises(ValueError):
            alignment_to_timestamps({'characters':['a'], 'character_start_times_seconds':[], 'character_end_times_seconds':[]})

    def test_transcript_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            normalize_timestamps('different words', [{'start':0,'end':1,'text':'hello'}], 1)

    def test_overlap_rejected(self):
        with self.assertRaises(ValueError):
            normalize_timestamps('hello world', [{'start':0,'end':.8,'text':'hello'},
                {'start':.5,'end':1,'text':'world'}], 1)

    def test_nonfinite_rejected(self):
        with self.assertRaises(ValueError):
            normalize_timestamps('hello', [{'start':0,'end':float('nan'),'text':'hello'}], 1)

    def test_coverage_gap_rejected(self):
        with self.assertRaises(ValueError):
            plan_scenes(self.units, [{'path':'a','end_index':1}], 1.2)

    def test_sentence_timing_not_fabricated(self):
        with self.assertRaises(ValueError):
            plan_scenes([Timing(0,1,'Hello world')], [{'path':'a','text':'Hello'}], 1)

    def test_scope_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError): _local('../outside.mp4',Path(td).resolve())


class VadTests(unittest.TestCase):
    def setUp(self):
        self.cfg=ListenerConfig('model','vad')

    def test_endpoint_after_256ms_silence(self):
        s=SpeechSegmenter(self.cfg)
        starts=[]
        for _ in range(8): starts.append(s.feed(b'frame',.9)[0])
        self.assertEqual(sum(starts),1)
        for _ in range(7): self.assertIsNone(s.feed(b'frame',0)[1])
        result=s.feed(b'frame',0)
        self.assertIsNotNone(result[1]);self.assertFalse(s.active)

    def test_short_noise_not_dispatched(self):
        s=SpeechSegmenter(self.cfg)
        for _ in range(2): s.feed(b'f',.9)
        results=[s.feed(b'f',0) for _ in range(8)]
        self.assertTrue(all(r[1] is None for r in results))

    def test_hysteresis(self):
        s=SpeechSegmenter(self.cfg)
        for _ in range(5): s.feed(b'f',.9)
        for _ in range(12): self.assertIsNone(s.feed(b'f',.45)[1])
        self.assertTrue(s.active)

    def test_overlong_command_not_partially_sent(self):
        s=SpeechSegmenter(ListenerConfig('m','v',max_utterance_seconds=2))
        results=[s.feed(b'f',.9) for _ in range(100)]
        self.assertTrue(any(r[2] for r in results))
        self.assertTrue(all(r[1] is None for r in results))
        self.assertTrue(s.reject_until_silence)
        for _ in range(8): s.feed(b'f',0)
        self.assertFalse(s.reject_until_silence)

    def test_invalid_latency_config(self):
        with self.assertRaises(ValueError):ListenerConfig('m','v',silence_ms=1000).validate()

    def test_mute_invalidates_pending_speech(self):
        listener=VoiceListener(self.cfg,lambda *args:None,lambda:None)
        listener.raw.put(('fixture',16000,0));listener.utterances.put(('fixture',0,0,'id'))
        listener.set_enabled(False)
        self.assertFalse(listener.enabled.is_set())
        self.assertTrue(listener.raw.empty());self.assertTrue(listener.utterances.empty())
        self.assertEqual(listener._epoch,1)
        listener.set_enabled(True);self.assertEqual(listener._epoch,2)

    def test_live_adapter_invalidates_late_turn(self):
        import sys
        from realtime_desktop import initialize,begin_turn,valid_turn
        d=N(root=N(after=lambda *a:None),closed=threading.Event(),health={})
        with patch.dict(sys.modules,{'onnxruntime':N(disable_telemetry_events=lambda:None)}):
            initialize(d)
        begin_turn(d);self.assertTrue(valid_turn(d));d.realtime_player.stop()
        self.assertFalse(valid_turn(d))

    def test_late_tts_after_barge_in_is_rejected(self):
        p=InterruptiblePlayer();token=p.begin_response();p.stop()
        self.assertFalse(p.play('nonexistent-late-audio.mp3',expected_epoch=token))

    def test_external_agent_endpoint_forbidden(self):
        with self.assertRaises(ValueError): LocalAgentClient('https://example.com')

    def test_transcription_exception_is_contained(self):
        import numpy as np
        listener=VoiceListener(self.cfg,lambda *args:self.fail('must not dispatch'),lambda:None)
        def fails(*args,**kwargs):
            raise RuntimeError('fixture')
        listener.model=N(transcribe=fails)
        listener.utterances.put((np.zeros(1600),0,__import__('time').monotonic(),'fixture'))
        t=threading.Thread(target=listener._transcribe);t.start()
        for _ in range(50):
            if listener.status()['stt_errors']:break
            listener.stop_event.wait(.01)
        listener.stop_event.set();t.join(1)
        self.assertEqual(listener.status()['stt_errors'],1)


class QueueTests(unittest.TestCase):
    def test_changed_source_is_blocked_before_encoder(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td)/'a.wav').write_bytes(b'fixture audio')
            (Path(td)/'a.png').write_bytes(b'fixture image')
            q=RenderQueue(td)
            m={'script_text':'hello','audio_path':'a.wav','timestamps':[],
               'media_assets':[{'path':'a.png','text':'hello'}],'output_path':'f.mp4'}
            j=q.create(m,'one');(Path(td)/'a.png').write_bytes(b'changed source')
            with patch('service_integration.render_video') as encoder:
                q.start()
                for _ in range(100):
                    if q.get(j['id'])['status']=='failed':break
                    q.stop_event.wait(.01)
                q.close();q.thread.join(2)
                encoder.assert_not_called()
            self.assertEqual(q.get(j['id'])['status'],'failed')

    def test_request_idempotency_and_changed_payload(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td)/'a.wav').write_bytes(b'fixture audio')
            (Path(td)/'a.png').write_bytes(b'fixture image')
            q=RenderQueue(td)
            m={'script_text':'hello','audio_path':'a.wav','timestamps':[{'start':0,'end':1,'text':'hello'}],
               'media_assets':[{'path':'a.png','text':'hello'}],'output_path':'final.mp4'}
            a=q.create(m,'same');self.assertEqual(a['id'],q.create(m,'same')['id'])
            with self.assertRaises(ValueError):q.create({**m,'script_text':'changed'},'same')

    def test_restart_does_not_replay_inflight_render(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td)/'a.wav').write_bytes(b'fixture audio')
            (Path(td)/'a.png').write_bytes(b'fixture image')
            q=RenderQueue(td)
            m={'script_text':'hello','audio_path':'a.wav','timestamps':[], 'media_assets':[{'path':'a.png','text':'hello'}], 'output_path':'f.mp4'}
            j=q.create(m,'one')
            with q._db() as c:c.execute("UPDATE renders SET status='running' WHERE id=?",(j['id'],))
            self.assertEqual(RenderQueue(td).get(j['id'])['status'],'interrupted')


@unittest.skipUnless(os.environ.get('ZOYA_TEST_4K')=='1','opt-in actual 4K encode')
class FFmpegTests(unittest.TestCase):
    def test_actual_4k_scene_cut_audio_and_subtitles(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            def cmd(*args):subprocess.run(['ffmpeg','-nostdin','-v','error',*args],check=True,capture_output=True)
            for color in ('red','blue'):
                cmd('-f','lavfi','-i',f'color={color}:size=3840x2160','-frames:v','1','-threads','1',str(root/f'{color}.png'))
            cmd('-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','1.2',str(root/'audio.wav'))
            fps=int(os.environ.get('ZOYA_TEST_FPS','30'));codec=os.environ.get('ZOYA_TEST_CODEC','h264')
            r=render_video('Hello world!', 'audio.wav',
                [{'start':.1,'end':.4,'text':'Hello'},{'start':.6,'end':1.0,'text':'world!'}],
                [{'path':'red.png','text':'Hello'},{'path':'blue.png','text':'world!'}],
                'final.mp4',workspace=root,fps=fps,codec=codec)
            self.assertEqual((r['width'],r['height'],r['fps']),(3840,2160,fps))
            self.assertTrue(r['technical_qa_passed']);self.assertLess(r['timing_error_seconds'],.05)
            # Verify that the selected scene really changes at its audio-bound boundary.
            for when, channel in ((.3,0),(.8,2)):
                raw=subprocess.run(['ffmpeg','-v','error','-ss',str(when),'-i',str(root/'final.mp4'),
                    '-vf','scale=2:2','-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'],capture_output=True,check=True).stdout
                self.assertGreater(raw[channel],180)
            self.assertTrue((root/'final.srt').is_file())
            with self.assertRaises(ValueError):
                render_video('hello','audio.wav',[],[],'final.mp4',workspace=root)


if __name__=='__main__':unittest.main()
