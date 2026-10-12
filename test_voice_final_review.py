import ast, io, json, sqlite3, tempfile, unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
import skales_bridge as bridge

class VoiceReviewTests(unittest.TestCase):
    def test_tool_timeout_is_not_a_completed_task(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'data').mkdir()
            calls=[]
            def request(path,body=None,method=None):
                calls.append(path)
                if path.endswith('/status'):
                    return io.BytesIO(b'{"provider":"ollama"}')
                return io.BytesIO(b'data: {"type":"text","content":"This answer was produced without tools: timeout"}\n\ndata: {"type":"done"}\n')
            with patch.object(bridge,'ROOT',root), patch.object(bridge,'request',request):
                result=bridge.dispatch('read-only fixture')
            self.assertEqual(result['status'],'needs_review')
            self.assertEqual(result['reason'],'local_model_tool_timeout')
            self.assertFalse(result['tool_execution_verified'])
            self.assertEqual(calls.count('/api/cli/chat'),1)
            with closing(sqlite3.connect(root/'data/desktop-tasks.sqlite3')) as db:
                self.assertEqual(db.execute('SELECT status FROM handoffs').fetchone()[0],'needs_review')

    def test_cloud_provider_does_not_receive_a_task(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'data').mkdir(); calls=[]
            def request(path,body=None,method=None):
                calls.append(path); return io.BytesIO(b'{"provider":"openai"}')
            with patch.object(bridge,'ROOT',root),patch.object(bridge,'request',request):
                result=bridge.dispatch('fixture')
            self.assertEqual(result['status'],'awaiting_local_provider')
            self.assertEqual(calls,['/api/cli/status'])

    def test_voice_failure_survives_final_listening_status(self):
        tree=ast.parse((Path(__file__).parent/'zoya_desktop.py').read_text(encoding='utf-8'))
        desktop=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Desktop')
        say=next(n for n in desktop.body if isinstance(n,ast.FunctionDef) and n.name=='say')
        namespace={}
        exec(compile(ast.Module(body=[say],type_ignores=[]),'say','exec'),namespace)
        class Fixture:
            health={}
            def post(self,*args): self.last=args
        fixture=Fixture()
        # Force an unavailable voice configuration without any network or audio call.
        namespace['load_config']=lambda: (_ for _ in ()).throw(ValueError('fixture unavailable'))
        namespace['ROOT']=Path('.')
        namespace['Path']=Path
        namespace['os']=__import__('os')
        namespace['json']=json
        namespace['wave']=__import__('wave')
        namespace['subprocess']=__import__('subprocess')
        namespace['time']=__import__('time')
        namespace['load_config']=lambda: (_ for _ in ()).throw(ValueError('fixture unavailable'))
        namespace['say'](fixture,'fixture reply')
        self.assertIn('fixture unavailable',fixture.health['last_voice_error'])
        reply=next(n for n in desktop.body if isinstance(n,ast.FunctionDef) and n.name=='reply')
        self.assertIn("self.health.get('last_voice_error'",ast.unparse(reply))

if __name__=='__main__': unittest.main()
