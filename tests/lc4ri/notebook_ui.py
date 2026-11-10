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

BASE = 'http://127.0.0.1:18888'
SOURCES = {
    'D00': 'D00_Prerequisites for Literate Computing via Notebooks.ipynb',
    'D10': 'D10_Hadoop - Set! Inventory.ipynb',
    'D12': 'D12_Hadoop - Ready! on CentOS6 - ZK,HDFS,YARN,HBase,Hive,Spark.ipynb',
    'T12a': 'T12a_Hadoop - Confirm the services are alive - ZK,HDFS,YARN,HBase,Spark.ipynb',
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
            'Service IP': address, 'VCPUs': '2', 'Memory(MiB)': '3072' if name != 'dn1' else '2048',
            'DFS Volumes': '1', 'YARN VCPUs': '1', 'YARN Total Memory(MB)': '512',
            'ZooKeeper': 'x', 'JournalNode': 'x', 'DataNode': 'x', 'NodeManager': 'x'})
        if name != 'dn1':
            row.update(NameNode='x', ResourceManager='x')
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
            'namenode_total_java_heap': '512m', 'namenode_heap_size': '512',
            'datanode_heap_size': '256', 'journalnode_heap_size': '128',
            'zkfc_heap_size': '128', 'zookeeper_heap_size': '128',
        }
        values.update(updates)
        edit_file(context, path, yaml.safe_dump(values, sort_keys=False),
                  'Choose verified archive mirror and JVM memory budgets for the declared VM sizes')
        assert all(values[k] == v for k, v in yaml.safe_load(original).items()), 'Generated topology changed'
        (results / 'generated-group-vars.yml').write_text(original)

    with sync_playwright() as pw:
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
                controls = operations(page, key, original, records, timeout=1200000)
                expected, executed = [], []
                for index, cell in enumerate(original['cells']):
                    text = source(cell)
                    if key in ('D12', 'T12a') and cell['cell_type'] == 'markdown' and text.startswith(('## YARNのインストール', '# YARNの状態確認')):
                        coverage[key] = dict(stop_before_heading=text.splitlines()[0], stop_before_cell=index)
                        break
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
            check_hdfs(results, context, records)
            passed.append('HDFS real service acceptance: HA states, three DataNodes, replicated file round trip and fsck')
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
            browser.close()


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
    address = ADDRESSES.get(address, address)
    page = context.new_page()
    page.goto('http://' + address + ':50070/dfshealth.html#tab-overview')
    page.wait_for_function("document.body.innerText.includes('Overview') && document.body.innerText.includes('Datanodes')", timeout=60000)
    page.screenshot(path=str(results / 'namenode-ui.png'), full_page=True)
    page.close()


if __name__ == '__main__':
    main()
