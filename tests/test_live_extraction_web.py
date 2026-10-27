import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import socket
from dataclasses import replace

from project_compass.candidate_extraction import validate_candidates
from project_compass.cli import _review_callback
from project_compass.extraction_jobs import ExtractionJobs
from project_compass.fact_engine import derive_facts
from project_compass.project_workspace import ProjectWorkspace
from project_compass.sample_data import sample_events
from project_compass.web_server import create_dashboard_server


class LiveExtractionWebTests(unittest.TestCase):
    def test_unconfigured_job_reads_small_delayed_body_before_returning_409(self):
        server=create_dashboard_server(sample_events(),port=0,sample_mode=True)
        entered=threading.Event()
        original=server.RequestHandlerClass
        class Handler(original):
            def do_POST(self):
                entered.set()
                super().do_POST()
        server.RequestHandlerClass=Handler
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with socket.create_connection(server.server_address,timeout=3) as connection:
                connection.sendall(b'POST /api/extract HTTP/1.0\r\nContent-Length: 2\r\n\r\n')
                self.assertTrue(entered.wait(2))
                connection.settimeout(.15)
                with self.assertRaises(socket.timeout):
                    connection.recv(1)
                connection.sendall(b'{}')
                connection.settimeout(3)
                body=b''
                while True:
                    part=connection.recv(4096)
                    if not part:break
                    body+=part
                self.assertIn(b'409 Conflict',body)
                self.assertIn(b'live extraction is not enabled',body)
        finally:
            server.shutdown();server.server_close();thread.join(3)

    def test_pending_job_does_not_block_review_or_capture_and_publishes_new_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = ProjectWorkspace(root/'state', root)
            first = replace(sample_events()[0],id='first',kind='assistant_message',content='Verify candidate v1.',metadata={'role':'assistant','member_id':'dev-a'})
            second = replace(first,id='second',content='Verify candidate v2.')
            def proposal(event, topic):
                return validate_candidates([event],{'candidates':[{'source_id':event.id,'kind':'task','subject_key':topic,'quote':event.content,'task_state':'pending'}]},'dev-a')
            workspace.save([first,second,*proposal(first,'v1')])
            events=workspace.load()
            fact=derive_facts(events)[0]
            entered, release=threading.Event(),threading.Event()
            class Provider:
                last_result={'cache_reused':False}
                def __init__(self,**kwargs): self.logger=kwargs['usage_logger']
                def extract(self,*args):
                    entered.set()
                    if not release.wait(4): raise AssertionError('test release missing')
                    self.logger({'total_tokens':25,'success':True})
                    return proposal(second,'v2')
                def acknowledge(self,events): pass
            jobs=ExtractionJobs(workspace,events,'dev-a',extractor_factory=Provider)
            review,relations,history,reviews=_review_callback(events,workspace,str(root),[],[],[])
            def refresh():
                workspace.save([replace(second,id='third',content='New user context.')])
                events[:]=workspace.load()
                return {'enabled':True,'updated':True}
            server=create_dashboard_server(events,port=0,extraction_jobs=jobs,review_callback=review,
                confirmed_relations=relations,review_history=history,fact_reviews=reviews,refresh_callback=refresh)
            thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            base=f'http://127.0.0.1:{server.server_address[1]}'
            def request(path,payload=None):
                req=urllib.request.Request(base+path,data=json.dumps(payload).encode() if payload is not None else None)
                with urllib.request.urlopen(req,timeout=3) as response: return response.status,json.load(response)
            try:
                self.assertEqual(request('/api/extract',{})[0],202)
                self.assertTrue(entered.wait(2))
                self.assertEqual(request('/api/review',{'action':'accept_fact','fact_id':fact.id,'reviewer':'synthetic-owner'})[0],200)
                self.assertEqual(request('/api/refresh',{})[0],200)
                release.set(); jobs.wait(4)
                _,state=request('/api/state')
                self.assertEqual(state['project']['fact_count'],2)
                self.assertEqual(len(state['fact_reviews']),1)
                self.assertIn('third', {e.id for e in events})
                self.assertEqual(state['extraction']['state'],'complete')
            finally:
                release.set(); server.shutdown(); server.server_close(); thread.join(3)

    def test_unconfigured_sample_rejects_model_job(self):
        server=create_dashboard_server(sample_events(),port=0,sample_mode=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            request=urllib.request.Request(f'http://127.0.0.1:{server.server_address[1]}/api/extract',data=b'{}')
            with self.assertRaises(urllib.error.HTTPError) as caught: urllib.request.urlopen(request,timeout=3)
            self.assertEqual(caught.exception.code,409)
        finally:
            server.shutdown();server.server_close();thread.join(3)
