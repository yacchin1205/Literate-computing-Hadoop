"""Act as an operator of the original notebooks; deployment stays in their cells."""
import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
from urllib.parse import quote, unquote, urlparse
import urllib.request

from playwright.sync_api import sync_playwright, expect
import yaml

from browser_evidence import recorded_context, finish_recording
from notebook_controls import operations

SECURITY_MODE = os.environ.get('HADOOP_SECURITY_MODE', 'simple')
assert SECURITY_MODE in ('simple', 'kerberos')
SECURE = SECURITY_MODE == 'kerberos'
BASE = 'http://127.0.0.1:18888'
SOURCES = {
    'D00': 'D00_Prerequisites for Literate Computing via Notebooks.ipynb',
    'D10': 'D10_Hadoop - Set! Inventory.ipynb',
    'D12': 'D12_Hadoop - Ready! on CentOS6 - ZK,HDFS,YARN,HBase,Hive,Spark.ipynb',
    'T12a': 'T12a_Hadoop - Confirm the services are alive - ZK,HDFS,YARN,HBase,Spark.ipynb',
    'T12d': 'T12d_Hadoop - Simple Spark script for Test.ipynb',
}
ADDRESSES = {'nn1': '172.29.88.10', 'nn2': '172.29.88.11', 'dn1': '172.29.88.12'}


def source(cell):
    value = cell['source']
    return ''.join(value) if isinstance(value, list) else value


def host_table(original):
    header = next(csv.reader(io.StringIO(original)))
    rows = []
    for name, address in ADDRESSES.items():
        row = dict.fromkeys(header, '')
        row.update(Cluster='TestCluster', Type='qemu', Name=name, **{
            'Service IP': address, 'VCPUs': '2', 'Memory(MiB)': '4096' if name != 'dn1' else '3072',
            'DFS Volumes': '1', 'YARN VCPUs': '1', 'YARN Total Memory(MB)': '1024',
            'ZooKeeper': 'x', 'JournalNode': 'x', 'DataNode': 'x', 'NodeManager': 'x'})
        if name != 'dn1':
            row.update(NameNode='x', ResourceManager='x')
        if name == 'nn1' and SECURE:
            row['KDC Master'] = 'x'
        if name == 'nn1':
            for role in ['Ganglia', 'TimelineService', 'MapReduce HistoryServer', 'HBase Master',
                         'HBase RegionServer', 'Tez', 'Hive', 'Pig', 'Client', 'Spark', 'Spark HistoryServer']:
                row[role] = 'x'
        rows.append(row)
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=header, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--result-dir', type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    results = args.result_dir.resolve()
    results.mkdir(parents=True, exist_ok=True)
    token = os.environ['LC4RI_TEST_TOKEN']
    originals = {key: (repo / path).read_bytes() for key, path in SOURCES.items()}
    templates = {key: json.loads(data) for key, data in originals.items()}
    hashes = {key: hashlib.sha256(data).hexdigest() for key, data in originals.items()}
    (results / 'source-hashes.json').write_text(json.dumps(hashes, indent=2) + '\n')
    records, passed, coverage = [], [], {}

    def save(status, error=None):
        (results / 'results.json').write_text(json.dumps(dict(status=status, error=error,
            passed=passed, operations=records, coverage=coverage), ensure_ascii=False, indent=2) + '\n')

    def api(path, method='GET', body=None):
        request = urllib.request.Request(BASE + '/api/contents/' + quote(path, safe='/'), method=method,
            headers={'Authorization': 'token ' + token, 'Content-Type': 'application/json'},
            data=None if body is None else json.dumps(body).encode())
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    for attempt in range(120):
        try:
            api('')
            break
        except Exception:
            time.sleep(1)
    else:
        raise AssertionError('LC4RI did not become ready')
    initial_inventory = '[seed]\n' + '\n'.join(ADDRESSES.values()) + '\n'
    api('inventory', 'PUT', dict(type='file', format='text', content=initial_inventory))
    api('ansible.cfg', 'PUT', dict(type='file', format='text', content='''[defaults]
inventory = /home/jovyan/work/inventory
remote_user = ansible
private_key_file = /home/jovyan/.ssh/id_rsa
interpreter_python = /usr/bin/python3
host_key_checking = True
log_path = /home/jovyan/work/ansible.log
[ssh_connection]
pipelining = True
'''))
    api('group_vars', 'PUT', dict(type='directory'))
    for key, template in templates.items():
        api(key + '-template.ipynb', 'PUT', dict(type='notebook', format='json', content=template))

    def edit_file(context, path, text, reason):
        before = api(path)['content']
        page = context.new_page()
        page.goto(BASE + '/edit/' + quote(path, safe='/') + '?token=' + token)
        page.locator('.CodeMirror').wait_for()
        page.locator('.CodeMirror').click()
        page.keyboard.press('Control+A')
        page.keyboard.insert_text(text)
        page.keyboard.press('Control+s')
        for attempt in range(30):
            if api(path)['content'] == text:
                break
            time.sleep(0.5)
        else:
            raise AssertionError('Editor did not save: ' + path)
        page.screenshot(path=str(results / (path.replace('/', '-') + '.png')), full_page=True)
        page.close()
        records.append(dict(action='edit file through browser', path=path, reason=reason, before=before, after=text))

    def group_vars(context):
        path = 'group_vars/hadoop_all_testcluster'
        original = api(path)['content']
        values = yaml.safe_load(original)
        updates = {
            'hadoop_release_url': 'http://172.29.88.1:18555/hadoop-3.2.2.tar.gz',
            'zookeeper_release_url': 'http://172.29.88.1:18555/apache-zookeeper-3.6.2-bin.tar.gz',
            'jsvc_release_url': 'http://172.29.88.1:18555/commons-daemon-1.2.4-src.tar.gz',
            'spark_release_url': 'http://172.29.88.1:18555/spark-3.1.1-bin-hadoop3.2.tgz',
            'resourcemanager_heap_size': '256', 'nodemanager_heap_size': '256',
            'job_historyserver_heapsize': '256', 'timelineserver_heap_size': '256',
            'spark_daemon_memory': '256m',
            'namenode_total_java_heap': '512m', 'namenode_heap_size': '512',
            'datanode_heap_size': '256', 'journalnode_heap_size': '128',
            'zkfc_heap_size': '128', 'zookeeper_heap_size': '128',
        }
        if SECURE:
            updates.update(kerberos_master_key_password=os.environ['KERBEROS_TEST_PASSWORD'],
                https_keystore_pass=os.environ['KERBEROS_TEST_PASSWORD'],
                https_truststore_pass=os.environ['KERBEROS_TEST_PASSWORD'],
                https_privkey_pass=os.environ['KERBEROS_TEST_PASSWORD'], datanode_https_port='50475')
        values.update(updates)
        edit_file(context, path, yaml.safe_dump(values, sort_keys=False),
                  'Choose verified archive mirror and JVM memory budgets for the declared VM sizes')
        assert all(values[k] == v for k, v in yaml.safe_load(original).items()), 'Generated topology changed'
        (results / 'generated-group-vars.yml').write_text(original)

    with sync_playwright() as pw:
        if SECURE:
            # Chromium disables ambient Kerberos auth in private contexts.
            size = {'width': 1600, 'height': 1000}
            profile = str(Path(os.environ['KRB5_CONFIG']).parent / 'chromium-profile')
            context = pw.chromium.launch_persistent_context(profile,
                args=['--auth-server-allowlist=nn1,nn2,dn1',
                      '--auth-negotiate-delegate-allowlist=nn1,nn2,dn1'],
                ignore_https_errors=True, viewport=size,
                record_video_dir=str(results / 'videos'), record_video_size=size)
            browser = context.browser
        else:
            browser = pw.chromium.launch()
            context = recorded_context(browser, results / 'videos', viewport={'width': 1600, 'height': 1000})
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = None
        try:
            for key, original in templates.items():
                template_page = context.new_page()
                page = template_page
                page.goto(BASE + '/notebooks/' + key + '-template.ipynb?token=' + token)
                page.wait_for_function('window.Jupyter && Jupyter.notebook && Jupyter.notebook.kernel && Jupyter.notebook.kernel.is_connected()', timeout=120000)
                page.locator('#menubar .dropdown-toggle').filter(has_text=re.compile(r'^File$')).click()
                with context.expect_page() as copied:
                    page.locator('#copy_notebook').click()
                page = copied.value
                page.set_default_timeout(60000)
                page.wait_for_function('window.Jupyter && Jupyter.notebook && Jupyter.notebook.kernel && Jupyter.notebook.kernel.is_connected()', timeout=120000)
                expect(page.locator('button:has(.fa-unfreeze)')).to_be_visible()
                document = unquote(urlparse(page.url).path.split('/notebooks/', 1)[1])
                assert document != key + '-template.ipynb'
                assert page.evaluate('() => Jupyter.notebook.get_cells().map(c => c.get_text())') == [source(c) for c in original['cells']]
                template_page.close()
                page.locator('#menubar .dropdown-toggle').filter(has_text=re.compile(r'^Cell$')).click()
                page.locator('#all_outputs > a').hover()
                page.locator('#clear_all_output > a').click()
                assert page.evaluate("() => Jupyter.notebook.get_cells().filter(c => c.cell_type === 'code').every(c => c.output_area.outputs.length === 0)")
                records.append(dict(notebook=key, action='Cell > All Output > Clear on working copy'))
                controls = operations(page, key, original, records, timeout=1200000)
                expected, executed = [], []
                for index, cell in enumerate(original['cells']):
                    text = source(cell)
                    # Explicit section boundaries: exercise YARN/Spark while leaving Tez/HBase/Hive out.
                    if key == 'D12' and index == 102:
                        coverage[key] = dict(stop_before_heading=text.splitlines()[0], stop_before_cell=index,
                                             skipped_sections=['Tez', 'HBase', 'Hive'])
                        break
                    if (key == 'D12' and 71 <= index <= 89) or (key == 'T12a' and 33 <= index <= 39):
                        continue
                    if cell['cell_type'] != 'code' or not text.strip():
                        continue
                    expected.append(index)
                    if key == 'D10' and 'hosts-old.csv hosts.csv' in text:
                        edit_file(context, 'hosts.csv', host_table(api('hosts.csv')['content']),
                                  'Enter the real three-VM placement design requested by D10')
                    if key == 'D10' and 'inventory-old inventory' in text:
                        generated = ''.join(o.get('text', '') for o in controls.state(controls.find('%run scripts/inventory-hadoop.py'))['outputs'])
                        generated = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', generated).replace('\r\n', '\n')
                        assert '[hadoop_all_testcluster:children]' in generated and all(ip in generated for ip in ADDRESSES.values()), generated
                        edit_file(context, 'inventory', initial_inventory + '\n' + generated,
                                  'Manually merge the Inventory emitted by the original D10 cell, preserving existing hosts')
                        (results / 'generated-inventory.txt').write_text(generated)
                    if key == 'D10' and text.startswith('archive_url ='):
                        controls.edit(index, text.replace("'http://xxx.xxx.nii.ac.jp:YYYY/v1/AUTH_XXXXXXXXXXXXXXXXXXXXXXXX/HDP-2.x/archive'", "'http://172.29.88.1:18555'"),
                                      'Enter the prerequisite archive server address')
                    if key == 'D10' and text.strip() == '!ls -la group_vars/':
                        group_vars(context)
                    if SECURE and key in ('D12', 'T12a', 'T12d') and index == 3:
                        controls.edit(index, text.replace("security_mode = 'simple'", "security_mode = 'kerberos'"),
                                      'Select the Kerberos variant declared by the workflow matrix')
                    if key == 'D12' and text.startswith('playbook_repository ='):
                        edited = text.replace("'https://github.com/NII-cloud-operation/Literate-computing-Hadoop.git'", "'file:///opt/test-source.git'")
                        edited = edited.replace('playbook_revision = None', 'playbook_revision = ' + repr(os.environ['LC4RI_TEST_REVISION']))
                        controls.edit(index, edited, 'Select the exact checked-out fork revision as the deployment source')
                    print(f'ORIGINAL PROCEDURE: {key} cell {index}', flush=True)
                    optional_diagnostic = key == 'D00' and index in (26, 32)
                    observed = controls.run(index, allow_error='RuntimeError' if optional_diagnostic else None)
                    executed.append(index)
                    output = json.dumps(observed['outputs'], ensure_ascii=False)
                    optional_diagnostic = key == 'D00' and index in (26, 32)
                    if not optional_diagnostic and re.search(r'FAILED!|UNREACHABLE!|failed=[1-9]|FAILED \||UNREACHABLE \||No inventory was parsed|provided hosts list is empty|Could not match supplied host pattern', output):
                        raise AssertionError(f'{key} cell {index}: shell/Ansible failure: {output[-5000:]}')
                    if optional_diagnostic:
                        records.append(dict(notebook=key, cell=index, action='inspect optional host diagnostic', output=observed['outputs']))
                    save('running')
                assert executed == expected
                coverage.setdefault(key, {}).update(executed_code_cells=executed,
                    excluded_code_cells=[i for i, c in enumerate(original['cells']) if c['cell_type'] == 'code' and source(c).strip() and i not in executed])
                page.keyboard.press('Control+s')
                page.wait_for_function('!Jupyter.notebook.dirty')
                saved = api(document)['content']
                assert len(saved['cells']) in (len(original['cells']), len(original['cells']) + 1)
                for index, cell in enumerate(saved['cells'][:len(original['cells'])]):
                    assert source(cell) == controls.edits.get(index, source(original['cells'][index])), (key, index)
                (results / (key + '-working.ipynb')).write_text(json.dumps(saved, ensure_ascii=False, indent=1) + '\n')
                page.screenshot(path=str(results / (key + '-completed.png')), full_page=True)
                page.close()
                page = None
                passed.append(key + ': original working-copy procedure completed within declared scope')
                save('running')
            prepare_browser_credentials()
            check_security(results, records)
            check_hdfs(results, context, records)
            passed.append('HDFS real service acceptance: HA states, three DataNodes, replicated file round trip and fsck')
            check_yarn_spark(results, context, records)
            passed.append('Spark on YARN: original T12d reads Iris from HDFS and writes verified results to HDFS')
            for key, data in originals.items():
                assert (repo / SOURCES[key]).read_bytes() == data, 'Tracked original changed: ' + key
                assert [source(c) for c in api(key + '-template.ipynb')['content']['cells']] == [source(c) for c in templates[key]['cells']]
            save('passed')
        except Exception as error:
            save('failed', str(error))
            if page is not None:
                page.screenshot(path=str(results / 'failure.png'), full_page=True)
                (results / 'failure.html').write_text(page.content())
                try:
                    failed = page.evaluate('() => Jupyter.notebook.toJSON()')
                    (results / 'failure-working.ipynb').write_text(json.dumps(failed, ensure_ascii=False, indent=1))
                except Exception:
                    pass
            raise
        finally:
            finish_recording(context, results / 'browser-trace.zip', results / 'videos')
            if browser is not None:
                browser.close()


def prepare_browser_credentials():
    if not SECURE:
        return
    keytab = Path(os.environ['KRB5_CONFIG']).parent / 'browser.keytab'
    command = ['scp', *shlex.split(os.environ['ANSIBLE_SSH_ARGS']),
               'ansible@172.29.88.10:/home/ansible/.hadoop.keytab', str(keytab)]
    subprocess.run(command, check=True, timeout=30)
    keytab.chmod(0o600)
    subprocess.run(['kinit', '-kt', str(keytab), 'ansible@TESTCLUSTER.ECLOUD.NII.AC.JP'], check=True, timeout=30)


def service_json(url):
    if SECURE:
        # Disposable self-signed test certificates are provisioned as prerequisites.
        return json.loads(subprocess.check_output(['/usr/bin/curl', '--silent', '--show-error', '--fail',
            '--insecure', '--negotiate', '-u', ':', url], text=True, timeout=60))
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


def check_security(results, records):
    ssh = ['ssh', *shlex.split(os.environ['ANSIBLE_SSH_ARGS']), 'ansible@172.29.88.10']
    actual = subprocess.check_output([*ssh, 'hdfs getconf -confKey hadoop.security.authentication'], text=True, timeout=60).strip()
    assert actual == ('kerberos' if SECURE else 'simple'), actual
    evidence = dict(matrix_mode=SECURITY_MODE, actual_authentication=actual)
    if SECURE:
        denied = subprocess.run([*ssh, 'env KRB5CCNAME=FILE:/tmp/nonexistent-ci-cache HADOOP_USER_NAME=hdfs hdfs dfs -ls /'],
                                capture_output=True, text=True, timeout=60)
        error = denied.stdout + denied.stderr
        assert denied.returncode != 0 and re.search(r'Kerberos|KERBEROS|credentials|TGT|SASL', error), error
        ticket = subprocess.check_output([*ssh, 'klist'], text=True, timeout=30)
        assert 'ansible@TESTCLUSTER.ECLOUD.NII.AC.JP' in ticket, ticket
        evidence.update(no_ticket_returncode=denied.returncode, no_ticket_output=error, client_ticket=ticket)
    (results / 'security-acceptance.json').write_text(json.dumps(evidence, indent=2) + '\n')
    records.append(dict(action='Verify actual security mode and reject unauthenticated HDFS access', **evidence))


def check_hdfs(results, context, records):
    """Independent acceptance only: all installation happened in original cells."""
    ssh = ['ssh', *shlex.split(os.environ['ANSIBLE_SSH_ARGS']), 'ansible@172.29.88.10']
    def hdfs(*args):
        command = shlex.join(['sudo', '-u', 'hdfs', 'env', 'HADOOP_HEAPSIZE=256', 'hdfs', *args])
        output = subprocess.check_output([*ssh, command], text=True, timeout=180)
        records.append(dict(action='HDFS acceptance command', command=command, output=output))
        return output
    states = hdfs('haadmin', '-getAllServiceState')
    assert len(re.findall(r'\bactive\b', states)) == 1 and len(re.findall(r'\bstandby\b', states)) == 1, states
    hdfs('dfsadmin', '-safemode', 'wait')
    report = hdfs('dfsadmin', '-report')
    assert 'Live datanodes (3)' in report, report
    data = bytes(range(256)) * 12288
    expected_hash = hashlib.sha256(data).hexdigest()
    subprocess.run([*ssh, 'cat > /tmp/hdfs-ui-input.bin'], input=data, check=True, timeout=30)
    hdfs('dfs', '-mkdir', '-p', '/ci')
    hdfs('dfs', '-Ddfs.blocksize=1048576', '-put', '/tmp/hdfs-ui-input.bin', '/ci/original-notebook.bin')
    hdfs('dfs', '-setrep', '-w', '3', '/ci/original-notebook.bin')
    hdfs('dfs', '-get', '/ci/original-notebook.bin', '/tmp/hdfs-ui-output.bin')
    digest = subprocess.check_output([*ssh, 'sha256sum /tmp/hdfs-ui-output.bin'], text=True, timeout=30).split()[0]
    assert digest == expected_hash, (digest, expected_hash)
    fsck = hdfs('fsck', '/ci/original-notebook.bin', '-files', '-blocks', '-locations')
    assert 'HEALTHY' in fsck and re.search(r'3 block\(s\)', fsck), fsck
    assert len(re.findall(r'Live_repl=3\b', fsck)) == 3, fsck
    (results / 'hdfs-acceptance.json').write_text(json.dumps(dict(states=states, report=report,
        expected_sha256=expected_hash, actual_sha256=digest, fsck=fsck), indent=2) + '\n')
    # Read the real NameNode UI as an operator after the T12a link confirmation.
    active = next(line.split()[0] for line in states.splitlines() if line.split()[-1] == 'active')
    address = active.rsplit(':', 1)[0]
    address = address if SECURE else ADDRESSES.get(address, address)
    if SECURE and address in ADDRESSES.values():
        address = next(name for name, ip in ADDRESSES.items() if ip == address)
    page = context.new_page()
    page.goto(('https://' + address + ':50470' if SECURE else 'http://' + address + ':50070') + '/dfshealth.html#tab-overview')
    check_namenode_ui(page, results)
    page.close()


def check_yarn_spark(results, context, records):
    """Read-only acceptance of the job and HDFS data created by original T12d."""
    ssh = ['ssh', *shlex.split(os.environ['ANSIBLE_SSH_ARGS']), 'ansible@172.29.88.10']
    def remote(command):
        output = subprocess.check_output([*ssh, command], text=True, timeout=90)
        records.append(dict(action='YARN/Spark acceptance command', command=command, output=output))
        return output
    def hdfs(*args):
        return remote(shlex.join(['hdfs', 'dfs', *args]))
    content = hdfs('-cat', '/dataset/iris/iris.data')
    lines = [line for line in content.splitlines() if line.strip()]
    species = {}
    for line in lines:
        name = line.split(',')[-1]
        species[name] = species.get(name, 0) + 1
    expected = dict(lines=len(lines), lines_with_a=sum('a' in line for line in lines),
                    lines_with_b=sum('b' in line for line in lines), species=species)
    assert expected == dict(lines=150, lines_with_a=100, lines_with_b=0,
        species={'Iris-setosa': 50, 'Iris-versicolor': 50, 'Iris-virginica': 50}), expected
    hdfs('-test', '-e', '/dataset/iris/result/_SUCCESS')
    actual = json.loads(hdfs('-cat', '/dataset/iris/result/part-*'))
    assert actual == expected, (actual, expected)
    states = {name: remote(shlex.join(['sudo', '-u', 'yarn', 'yarn', 'rmadmin', '-getServiceState', name])).strip()
              for name in ['nn1', 'nn2']}
    assert sorted(states.values()) == ['active', 'standby'], states
    active = next(name for name, state in states.items() if state == 'active')
    base = 'https://' + active + ':8088' if SECURE else 'http://' + ADDRESSES[active] + ':8088'
    def get(path):
        return service_json(base + path)
    nodes = get('/ws/v1/cluster/nodes')['nodes']['node']
    assert len(nodes) == 3 and all(node['state'] == 'RUNNING' for node in nodes), nodes
    apps = get('/ws/v1/cluster/apps?applicationTypes=SPARK')['apps']['app']
    assert len(apps) == 1, apps
    app = apps[0]
    # YARN records the submission filename; the driver sets its Spark name later.
    assert app['name'] == 'SimpleApp.py' and app['state'] == 'FINISHED' and app['finalStatus'] == 'SUCCEEDED', app
    # The completed application must have actually allocated YARN containers.
    assert app['memorySeconds'] > 0 and app['vcoreSeconds'] > 0, app
    logs = remote(shlex.join(['yarn', 'logs', '-applicationId', app['id']]))
    assert 'Lines with a: 100, lines with b: 0' in logs, logs[-4000:]
    (results / 'spark-yarn-application.log').write_text(logs)
    (results / 'yarn-spark-acceptance.json').write_text(json.dumps(dict(
        input_sha256=hashlib.sha256(content.encode()).hexdigest(), expected=expected, actual=actual,
        rm_states=states, nodes=nodes, application=app), indent=2) + '\n')
    page = context.new_page()
    page.goto(base + '/cluster/app/' + app['id'])
    expect(page.locator('body')).to_contain_text(app['id'], timeout=60000)
    expect(page.locator('body')).to_contain_text('SUCCEEDED')
    page.screenshot(path=str(results / 'yarn-application.png'), full_page=True)
    page.goto('http://172.29.88.10:18080')
    # History Server polls HDFS; wait for the completed application to be indexed.
    for attempt in range(30):
        history = service_json('http://172.29.88.10:18080/api/v1/applications')
        if any(item['id'] == app['id'] and item['attempts'][0]['completed'] for item in history):
            break
        time.sleep(2)
    else:
        raise AssertionError('Completed Spark application missing from History Server')
    page.reload()
    expect(page.locator('body')).to_contain_text('test-pyspark', timeout=60000)
    page.screenshot(path=str(results / 'spark-history.png'), full_page=True)
    (results / 'spark-history.json').write_text(json.dumps(history, indent=2) + '\n')
    page.close()


def check_namenode_ui(page, results):
    # Navigation is available before JMX/Dust render the actual service data.
    overview = page.locator('#tab-overview')
    expect(overview).to_contain_text('Cluster ID:', timeout=60000)
    expect(overview).to_contain_text('3.2.2')
    expect(overview).to_contain_text('(active)')
    live_row = overview.locator('tr').filter(has=page.locator('th a').filter(has_text=re.compile(r'^Live Nodes$')))
    expect(live_row.locator('td')).to_have_text(re.compile(r'^3\s+\('))
    overview_text = overview.inner_text()
    page.screenshot(path=str(results / 'namenode-ui.png'), full_page=True)
    page.locator('#ui-tabs a[href="#tab-datanode"]').click()
    rows = page.locator('#table-datanodes tbody tr')
    expect(rows).to_have_count(3, timeout=60000)
    expect(page.locator('#table-datanodes tbody tr.danger')).to_have_count(0)
    for row in rows.all():
        expect(row).to_contain_text('3.2.2')
    nodes_text = rows.all_inner_texts()
    for address in ADDRESSES.values():
        assert any(address in text for text in nodes_text), (address, nodes_text)
    page.screenshot(path=str(results / 'datanodes-ui.png'), full_page=True)
    (results / 'namenode-ui.json').write_text(json.dumps(dict(
        overview=overview_text, datanode_rows=nodes_text), indent=2) + '\n')


if __name__ == '__main__':
    main()
