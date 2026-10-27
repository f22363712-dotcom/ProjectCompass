"""Bounded-process JDBC bridge. Optional Java packages are loaded only in main."""
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlparse

PROTOCOL = 'projectcompass-zetaris-v1'


def build_select(plan):
    if not re.fullmatch(r'pc_[0-9a-f]{16}', plan['database']):
        raise ValueError('invalid project database')
    keys = plan['keys']
    if not keys or any(not re.fullmatch('[0-9a-f]{64}', key) for key in keys):
        raise ValueError('invalid source keys')
    filters = ','.join("'" + key + "'" for key in keys)
    parts = []
    for source in plan['sources']:
        if not re.fullmatch(r'evidence_[0-9a-f]{16}_(project|messages)', source['table']):
            raise ValueError('invalid source table')
        parts.append(f"SELECT r.source_key, r.payload_json, r.digest, r.family FROM {plan['database']}.{source['table']} "
                     f'LATERAL VIEW explode(records) AS r WHERE r.source_key IN ({filters})')
    return ' UNION ALL '.join(parts)


def execute_statement(connection, sql):
    statement = connection.createStatement()
    resultset = None
    try:
        columns, rows = [], []
        if statement.execute(sql):
            resultset = statement.getResultSet()
            metadata = resultset.getMetaData()
            columns = [str(metadata.getColumnLabel(i)) for i in range(1, metadata.getColumnCount() + 1)]
            while resultset.next():
                rows.append([None if resultset.getString(i) is None else str(resultset.getString(i))
                             for i in range(1, len(columns) + 1)])
        return {'columns': columns, 'rows': rows}
    finally:
        if resultset is not None:
            resultset.close()
        statement.close()


def execute_plan(execute, plan):
    sql = build_select(plan)
    database = plan['database']

    def databases():
        result = execute('SHOW LIGHTNING DATABASES')
        return {row[result['columns'].index('name')].casefold() for row in result['rows']}

    def tables():
        result = execute(f'SHOW LIGHTNING TABLES {database}')
        return {row[result['columns'].index('name')]: row[result['columns'].index('source_table')] for row in result['rows']}

    if database.casefold() not in databases():
        try:
            execute(f'CREATE LIGHTNING DATABASE {database} DESCRIBE BY "ProjectCompass evidence"')
        except Exception:
            # A competing first query may have committed the same database.
            # Do not retry SQL; accept only a now-observable matching identity.
            if database.casefold() not in databases():
                raise
    known = tables()
    created = 0
    for source in plan['sources']:
        endpoint = source['endpoint']
        parsed = urlparse(endpoint)
        if parsed.scheme != 'http' or parsed.hostname != 'host.docker.internal' or parsed.path != '/api/zetaris-sources' or '"' in endpoint:
            raise ValueError('unexpected local source endpoint')
        if source['table'] in known:
            if known[source['table']] != endpoint:
                raise ValueError('existing source definition differs')
        else:
            try:
                execute(f"CREATE LIGHTNING REST TABLE {source['table']} FROM {database} REQUEST ("
                        f'endpoint "{endpoint}", method "GET", http_encoding "URLENCODED", '
                        'response_type "JSON", insecure_ssl "false") HEADER () BODY ()')
            except Exception:
                if tables().get(source['table']) != endpoint:
                    raise
            else:
                created += 1
    return {**execute(sql), 'created_sources': created}


def main():
    started = time.perf_counter()
    connection = None
    jvm = None
    phase = 'configuration'
    try:
        plan = json.load(sys.stdin)
        bundle = Path(plan['bundle'])
        settings = {}
        for line in (bundle / '.env').read_text(encoding='utf8').splitlines():
            if line.startswith(('ZETARIS_ADMIN_EMAIL=', 'ZETARIS_ADMIN_PASSWORD=', 'DRIVER_PORT=')):
                key, value = line.split('=', 1)
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                settings[key] = value
        port = int(settings['DRIVER_PORT'])
        if not 0 < port < 65536 or not settings['ZETARIS_ADMIN_EMAIL'] or not settings['ZETARIS_ADMIN_PASSWORD']:
            raise ValueError('invalid local JDBC configuration')
        phase = 'connect'
        import jaydebeapi
        import jpype
        jvm = jpype
        jar = bundle / 'jdbc/ndp-jdbc-driver-2.4.3.1-7eff043-driver.jar'
        jpype.startJVM('-Xmx128m', '-Dsun.net.client.defaultConnectTimeout=10000',
                      '-Dsun.net.client.defaultReadTimeout=10000', classpath=[str(jar)])
        connection = jaydebeapi.connect('com.zetaris.lightning.jdbc.LightningDriver',
            f'jdbc:zetaris:lightning@localhost:{port}',
            [settings['ZETARIS_ADMIN_EMAIL'], settings['ZETARIS_ADMIN_PASSWORD']])
        phase = 'query'
        result = execute_plan(lambda sql: execute_statement(connection.jconn, sql), plan)
        print(json.dumps({'protocol': PROTOCOL, 'success': True, **result,
                          'elapsed_ms': round((time.perf_counter() - started) * 1000)}), flush=True)
        return 0
    except Exception as exc:
        # Exception messages/Java logs may contain credentials; expose only type.
        print(json.dumps({'protocol': PROTOCOL, 'success': False, 'phase': phase,
                          'error_type': type(exc).__name__}), flush=True)
        return 2
    finally:
        if connection is not None:
            connection.close()
        if jvm is not None and jvm.isJVMStarted():
            jvm.shutdownJVM()


if __name__ == '__main__':
    raise SystemExit(main())
