import unittest

from project_compass.zetaris_worker import execute_plan, execute_statement


class ZetarisWorkerTests(unittest.TestCase):
    def test_competing_registration_rediscovers_and_verifies_committed_definition(self):
        endpoint = 'http://host.docker.internal:18791/api/zetaris-sources?revision=abc&family=project'
        plan = {'database': 'pc_' + 'a'*16, 'sources': [{'table': 'evidence_' + 'b'*16 + '_project', 'endpoint': endpoint}], 'keys': ['c'*64]}
        committed = {'database': False, 'table': False}
        creates = []
        def execute(sql):
            if sql == 'SHOW LIGHTNING DATABASES':
                return {'columns': ['name'], 'rows': [[plan['database']]] if committed['database'] else []}
            if sql.startswith('SHOW LIGHTNING TABLES'):
                return {'columns': ['name', 'source_table'], 'rows': [[plan['sources'][0]['table'], endpoint]] if committed['table'] else []}
            if sql.startswith('CREATE'):
                creates.append(sql)
                committed['database' if sql.startswith('CREATE LIGHTNING DATABASE') else 'table'] = True
                raise RuntimeError('another request just committed the same metadata')
            return {'columns': [], 'rows': []}
        result = execute_plan(execute, plan)
        self.assertEqual(result['created_sources'], 0)
        self.assertEqual(len(creates), 2)

    def test_competing_mismatched_definition_still_fails(self):
        endpoint = 'http://host.docker.internal:18791/api/zetaris-sources?revision=abc&family=project'
        plan = {'database': 'pc_' + 'a'*16, 'sources': [{'table': 'evidence_' + 'b'*16 + '_project', 'endpoint': endpoint}], 'keys': ['c'*64]}
        seen = []
        def execute(sql):
            if sql == 'SHOW LIGHTNING DATABASES':
                return {'columns': ['name'], 'rows': [[plan['database']]]}
            if sql.startswith('SHOW LIGHTNING TABLES'):
                return {'columns': ['name', 'source_table'], 'rows': [[plan['sources'][0]['table'], endpoint + '&wrong=1']] if seen else []}
            seen.append(sql)
            raise RuntimeError('creation failed')
        with self.assertRaises(RuntimeError):
            execute_plan(execute, plan)

    def test_existing_matching_source_is_reused_and_query_has_no_raw_input(self):
        endpoint = 'http://host.docker.internal:18791/api/zetaris-sources?revision=abc&family=project'
        plan = {'database': 'pc_' + 'a'*16, 'sources': [{'table': 'evidence_' + 'b'*16 + '_project', 'endpoint': endpoint}], 'keys': ['c'*64]}
        calls = []
        def execute(sql):
            calls.append(sql)
            if sql == 'SHOW LIGHTNING DATABASES':
                return {'columns': ['name'], 'rows': [[plan['database']]]}
            if sql.startswith('SHOW LIGHTNING TABLES'):
                return {'columns': ['name', 'source_table'], 'rows': [[plan['sources'][0]['table'], endpoint]]}
            return {'columns': [], 'rows': []}
        result = execute_plan(execute, plan)
        self.assertEqual(result['created_sources'], 0)
        self.assertEqual(len(calls), 3)
        self.assertIn('explode(records)', calls[-1])
        self.assertNotIn('CREATE', '\n'.join(calls))
        plan['sources'][0]['endpoint'] += '&changed=true'
        with self.assertRaises(ValueError):
            execute_plan(execute, plan)

    def test_jdbc_uses_ordinary_statement_and_closes_it(self):
        calls = []
        class Statement:
            def execute(self, sql):
                calls.append(sql)
                return False
            def close(self):
                calls.append('close')
        class Connection:
            def createStatement(self):
                return Statement()
            def prepareStatement(self, *args):
                raise AssertionError('prepared statement used')
        self.assertEqual(execute_statement(Connection(), 'CREATE REST "?id=x"'), {'columns': [], 'rows': []})
        self.assertEqual(calls, ['CREATE REST "?id=x"', 'close'])
