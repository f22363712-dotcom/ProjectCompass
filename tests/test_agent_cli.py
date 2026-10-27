import io
import json
import os
from contextlib import redirect_stdout,redirect_stderr
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from project_compass.cli import main


class AgentCliTests(unittest.TestCase):
    def test_private_config_is_project_bound_and_not_printed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);project=root/'project';project.mkdir();config=root/'private.json'
            config.write_text(json.dumps({'schema_version':1,'project_path':str(project),'owner_id':'owner','clients':[
                {'client_id':'a','member_id':'agent-a','session_id':'session-a','agent':'codex','token_env':'PROJECT_COMPASS_TEST_AGENT_TOKEN'}]}))
            output,error=io.StringIO(),io.StringIO()
            with patch.dict(os.environ,{'PROJECT_COMPASS_TEST_AGENT_TOKEN':'a'*40}),patch('project_compass.cli.run_dashboard') as serve,redirect_stdout(output),redirect_stderr(error):
                code=main(['--scan',str(project),'--web','--agent-access-file',str(config),
                    '--workspace-root',str(root/'state'),'--env-file',str(root/'missing.env')])
            self.assertEqual(code,0);self.assertEqual(serve.call_args.kwargs['agent_inbox'].owner_id,'owner')
            self.assertNotIn('a'*40,output.getvalue()+error.getvalue())
            with redirect_stdout(output),redirect_stderr(error):
                code=main(['--sample','--web','--agent-access-file',str(config),'--env-file',str(root/'missing.env')])
            self.assertEqual(code,2)
