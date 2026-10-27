import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from project_compass.codex_extractor import CodexExtractor, INSTRUCTIONS
from project_compass.extraction_jobs import ExtractionJobs
from project_compass.project_workspace import ProjectWorkspace
from project_compass.web_server import create_dashboard_server
import test_personal_extraction as personal_fixture


class PersonalExtractionWebTests(unittest.TestCase):
    def start(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name);w=ProjectWorkspace(root/'state',root)
        helper=personal_fixture.PersonalExtractionTests()
        w.save([helper.source('a','Alice','Check A.'),helper.source('b','Bob','Check B.')])
        calls=[];run=helper.runner(calls)
        def factory(**kwargs):return CodexExtractor(command='offline-fake',runner=run,**kwargs)
        jobs=ExtractionJobs(w,w.load(),'Alice',extractor_factory=factory,max_batches=2)
        self.addCleanup(jobs.close)
        server=create_dashboard_server(jobs.events,port=0,extraction_jobs=jobs)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(thread.join,3);self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_address[1]}',jobs,calls

    def post(self,base,payload):
        request=urllib.request.Request(base+'/api/extract',data=None if payload is None else json.dumps(payload).encode(),method='POST')
        try:
            with urllib.request.urlopen(request,timeout=4) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as error:return error.code,json.load(error)

    def test_post_member_scope_is_applied_and_invalid_requests_do_not_spend_budget(self):
        base,jobs,calls=self.start()
        for payload in ({'member_id':'missing'},{'member_id':[]},{'member_id':None},{'max_batches':100}):
            with self.subTest(payload=payload):
                self.assertEqual(self.post(base,payload)[0],400)
                self.assertEqual(jobs.status()['batches_used'],0)
        self.assertEqual(calls,[])
        self.assertEqual(self.post(base,{'member_id':'Bob'})[0],202)
        jobs.wait(3)
        self.assertEqual([record['id'] for record in calls[0]],['b'])
        self.assertEqual(jobs.status()['scope'],{'kind':'member','member_id':'Bob'})

    def test_zero_body_post_preserves_default_project_extraction(self):
        base,jobs,calls=self.start()
        self.assertEqual(self.post(base,None)[0],202);jobs.wait(3)
        self.assertEqual({record['id'] for record in calls[0]},{'a','b'})
        self.assertEqual(jobs.status()['scope'],{'kind':'project'})


if __name__=='__main__':unittest.main()
