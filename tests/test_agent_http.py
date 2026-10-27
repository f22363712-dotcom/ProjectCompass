import inspect
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from project_compass.agent_inbox import AgentInbox
from project_compass.dashboard import build_dashboard_state
from project_compass.project_workspace import ProjectWorkspace
from project_compass.web_server import create_dashboard_server
from project_compass.web_server import _DashboardHandler


class AgentHttpTests(unittest.TestCase):
    def test_early_denial_drains_only_a_bounded_body_before_responding(self):
        self.assertTrue(hasattr(_DashboardHandler,'_reject'))
        class Connection:
            def settimeout(self,value):pass
        for declared,body,expected in ((12,b'bounded-body',12),(270000,b'large',5),(2000000,b'large',0)):
            handler=object.__new__(_DashboardHandler)
            handler.command='POST';handler.headers={'Content-Length':str(declared)}
            handler.rfile=io.BytesIO(body);handler.connection=Connection()
            positions=[];handler._json=lambda status,payload:positions.append(handler.rfile.tell())
            handler._reject(401,{'error':'credential required'})
            self.assertEqual(positions,[expected])

    def setUp(self):
        self.assertIn('agent_inbox',inspect.signature(create_dashboard_server).parameters)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);self.project=root/'project';self.project.mkdir()
        self.workspace=ProjectWorkspace(root/'state',self.project);self.workspace.save([]);self.events=[]
        self.inbox=AgentInbox({'schema_version':1,'project_path':str(self.project),'owner_id':'owner','clients':[
            {'client_id':'a','member_id':'agent-a','session_id':'session-a','agent':'codex','token':'a'*40}]},self.workspace,self.events)
        self.review_calls=[]
        def review(action,target,owner):
            self.review_calls.append((action,target,owner))
            return build_dashboard_state(self.events,str(self.project))
        self.server=create_dashboard_server(self.events,port=0,project_path=str(self.project),agent_inbox=self.inbox,review_callback=review)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_address[1]}'
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join(3)

    def request(self,path,payload=None,headers=None):
        request=Request(self.base+path,data=json.dumps(payload).encode() if payload is not None else None,
                        headers={'Content-Type':'application/json',**(headers or {})})
        try:
            response=urlopen(request,timeout=5)
        except HTTPError as error:response=error
        with response:return response.status,dict(response.headers),response.read()

    def event(self):
        return {'schema_version':1,'events':[{'id':'one','type':'assistant_message','timestamp':'2026-10-25T08:00:00Z','content':'Final package verification remains pending.'}]}

    def test_authenticated_append_propose_read_and_replay(self):
        headers={'Authorization':'Bearer '+'a'*40}
        status,_,raw=self.request('/api/agent/events',self.event(),headers)
        self.assertEqual(status,200);result=json.loads(raw);self.assertEqual(result['appended'],1)
        source=result['ids'][0]
        proposal={'schema_version':1,'proposals':[{'id':'p-one','source_id':source,'kind':'task','subject_key':'package-check','quote':self.event()['events'][0]['content']}]}
        status,_,raw=self.request('/api/agent/proposals',proposal,headers);self.assertEqual(status,200)
        self.assertEqual(json.loads(raw)['appended'],1)
        before=self.workspace.state_path.read_bytes()
        status,_,raw=self.request('/api/agent/events',self.event(),headers);self.assertEqual(json.loads(raw)['appended'],0)
        self.assertEqual(before,self.workspace.state_path.read_bytes())
        status,_,raw=self.request('/api/agent/state',headers=headers);self.assertEqual(status,200)
        state=json.loads(raw);self.assertIn('agent-a',state['personal']);self.assertEqual(state['project']['fact_count'],1)
        self.assertNotIn('a'*40,raw.decode())

    def test_unauthorized_and_cross_origin_writes_do_not_change_state(self):
        before=self.workspace.state_path.read_bytes()
        for headers,expected in (({},401),({'Authorization':'Bearer wrong'},401),
                                 ({'Authorization':'Bearer '+'a'*40,'Origin':'https://untrusted.invalid'},403)):
            status,_,raw=self.request('/api/agent/events',self.event(),headers)
            self.assertEqual(status,expected);self.assertNotIn('a'*40,raw.decode())
        self.assertEqual(before,self.workspace.state_path.read_bytes());self.assertEqual(self.events,[])

    def test_owner_browser_channel_and_agent_denial(self):
        status,headers,_=self.request('/');self.assertEqual(status,200)
        cookie=headers['Set-Cookie'];self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=Strict',cookie)
        owner={'Cookie':cookie.split(';')[0],'Origin':self.base}
        payload={'action':'accept_fact','fact_id':'synthetic','reviewer':'owner'}
        for extra in ({'Authorization':'Bearer '+'a'*40},{'Origin':'https://untrusted.invalid'}):
            status,_,_=self.request('/api/review',payload,{**owner,**extra});self.assertEqual(status,403)
        for action in ('confirm_abandoned','confirm_deferred','restore_direction'):
            status,_,_=self.request('/api/review',{**payload,'action':action},{**owner,'Authorization':'Bearer '+'a'*40})
            self.assertEqual(status,403)
        status,_,_=self.request('/api/review',{**payload,'reviewer':'someone-else'},owner);self.assertEqual(status,403)
        status,_,_=self.request('/api/review',payload,owner);self.assertEqual(status,200)
        self.assertEqual(self.review_calls,[('accept_fact','synthetic','owner')])
        status,_,raw=self.request('/api/state');self.assertEqual(status,200)
        self.assertNotIn(cookie.split('=')[1].split(';')[0],raw.decode())

    def test_owner_cookie_bootstrap_rejects_foreign_host_and_cross_site(self):
        for headers in ({'Host':'untrusted.invalid'},{'Sec-Fetch-Site':'cross-site'}):
            status,response_headers,_=self.request('/',headers=headers)
            self.assertEqual(status,403);self.assertNotIn('Set-Cookie',response_headers)

    def test_invalid_body_and_disabled_route_are_bounded(self):
        headers={'Authorization':'Bearer '+'a'*40}
        status,_,_=self.request('/api/agent/events',{'schema_version':1,'events':'x'*270000},headers)
        self.assertEqual(status,413)
        status,_,_=self.request('/api/agent/events',{'schema_version':1,'events':[{'content':[]} ]},headers)
        self.assertEqual(status,400)
        self.server.agent_inbox=None
        status,_,_=self.request('/api/agent/events',self.event(),headers);self.assertEqual(status,404)
