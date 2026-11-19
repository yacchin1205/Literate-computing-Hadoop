"""Operator steps for the disposable Hadoop cluster."""
import csv
import io
import json
import os
import re

import yaml

MIRROR = 'http://172.29.88.1:18555'
ADDRESSES = {'nn1': '172.29.88.10', 'nn2': '172.29.88.11', 'dn1': '172.29.88.12'}


def host_table(template, security):
    header = next(csv.reader(io.StringIO(template)))
    rows = []
    for name, address in ADDRESSES.items():
        row = dict.fromkeys(header, '')
        row.update(Cluster='TestCluster', Type='qemu', Name=name, **{
            'Service IP': address, 'VCPUs': '2', 'Memory(MiB)': '6144' if name != 'dn1' else '4096',
            'DFS Volumes': '1', 'YARN VCPUs': '2',
            'YARN Total Memory(MB)': '4096' if name != 'dn1' else '2048',
            'ZooKeeper': 'x', 'JournalNode': 'x', 'DataNode': 'x', 'NodeManager': 'x'})
        if name != 'dn1':
            row.update(NameNode='x', ResourceManager='x')
        if name == 'nn1':
            for role in ['Ganglia', 'TimelineService', 'MapReduce HistoryServer', 'HBase Master',
                         'HBase RegionServer', 'Pig', 'Client', 'Spark', 'Spark HistoryServer']:
                row[role] = 'x'
            if security:
                row['KDC Master'] = 'x'
        rows.append(row)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=header, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def enter_inventory_inputs(session, controls, index, text):
    if text.startswith('!diff -ur ') and 'hosts-old.csv' in text:
        session.edit_file('hosts.csv', host_table(session.read_file('hosts.csv'),
                                                os.environ['HADOOP_SECURITY_MODE'] == 'kerberos'))
    if text.startswith('!diff -ur ') and 'inventory-old' in text:
        outputs = controls.state(controls.find('%run scripts/inventory-hadoop.py'))['outputs']
        generated = ''.join(output['text'] for output in outputs if output['output_type'] == 'stream')
        generated = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', generated).replace('\r\n', '\n')
        session.edit_file('inventory', session.read_file('inventory') + '\n' + generated)
    if text.startswith('archive_url ='):
        controls.edit(index, 'archive_url = ' + repr(MIRROR), 'Enter the archive server')
    if text.strip() == '!ls -la group_vars/':
        path = 'group_vars/hadoop_all_testcluster'
        values = yaml.safe_load(session.read_file(path))
        manifest = json.loads((session.results / 'package-manifest.json').read_text())
        values.update({item['package'] + '_release_url': MIRROR + '/' + item['filename'] for item in manifest if not item['package'].endswith('_upgrade')})
        values.update(resourcemanager_heap_size='256', nodemanager_heap_size='256',
                      job_historyserver_heapsize='256', timelineserver_heap_size='256',
                      namenode_total_java_heap='512m', namenode_heap_size='512',
                      datanode_heap_size='256', journalnode_heap_size='128', zkfc_heap_size='128',
                      zookeeper_heap_size='128')
        if os.environ['HADOOP_SECURITY_MODE'] == 'kerberos':
            password = os.environ['KERBEROS_TEST_PASSWORD']
            values.update(kerberos_master_key_password=password, https_keystore_pass=password,
                          https_truststore_pass=password, https_privkey_pass=password, datanode_https_port='50475')
        session.edit_file(path, yaml.safe_dump(values, sort_keys=False))


def run(session):
    mode = os.environ['HADOOP_SECURITY_MODE']
    assert mode in ('simple', 'kerberos'), mode
    session.api('contents/inventory', 'PUT', dict(type='file', format='text',
                content='[seed]\n' + '\n'.join(ADDRESSES.values()) + '\n'))
    session.api('contents/ansible.cfg', 'PUT', dict(type='file', format='text', content='''[defaults]
inventory = /home/jovyan/work/inventory
remote_user = ansible
private_key_file = /home/jovyan/.ssh/id_rsa
interpreter_python = /usr/bin/python3
host_key_checking = True
log_path = /home/jovyan/work/ansible.log
[ssh_connection]
pipelining = True
'''))
    session.api('contents/group_vars', 'PUT', dict(type='directory'))

    run_notebook = session.run_notebook
    run_notebook('D00_Prerequisites for Literate Computing via Notebooks.ipynb')
    run_notebook('D10_Hadoop - Set! Inventory.ipynb', before_cell=enter_inventory_inputs)
    run_notebook('D12_Hadoop - Ready! on CentOS6 - ZK,HDFS,YARN,HBase,Hive,Spark.ipynb')
    run_notebook('T12a_Hadoop - Confirm the services are alive - ZK,HDFS,YARN,HBase,Spark.ipynb')
    run_notebook('T12c_Hadoop - Simple HBase query for Test.ipynb')
    hbase_book = json.loads((session.results / 'T12c-working.ipynb').read_text())
    hbase_output = '\n'.join(''.join(output.get('text', []))
                             for cell in hbase_book['cells']
                             for output in cell.get('outputs', []))
    for value in ('data:class', 'value=Iris-setosa', 'data:sepal_length', 'value=5.4'):
        assert value in hbase_output, hbase_output
    run_notebook('T12d_Hadoop - Simple Spark script for Test.ipynb')
    if os.environ.get('HADOOP_TEST_SCENARIO') == 'upgrade':
        run_upgrade(session)


def run_upgrade(session):
    from upgrade_observer import UpgradeObserver
    from upgrade_inputs import enter_upgrade_inputs, inspect_upgrade_output
    session.upgrade = {}
    observer = UpgradeObserver(session.results)
    run_notebook = session.run_notebook
    try:
        observer.begin()
        run_notebook('O12e_Hadoop - Plan and prepare upgrade.ipynb', before_cell=enter_upgrade_inputs, after_cell=inspect_upgrade_output)
        session.upgrade_zookeepers = set()
        run_notebook('O12f_Hadoop - Upgrade ZooKeeper.ipynb', before_cell=enter_upgrade_inputs, after_cell=inspect_upgrade_output, instance='first')
        run_notebook('O12f_Hadoop - Upgrade ZooKeeper.ipynb', before_cell=enter_upgrade_inputs, after_cell=inspect_upgrade_output, instance='second')
        run_notebook('O12f_Hadoop - Upgrade ZooKeeper.ipynb', before_cell=enter_upgrade_inputs, after_cell=inspect_upgrade_output, instance='third')
        run_notebook('O12g_Hadoop - Rolling upgrade NameNodes.ipynb', before_cell=enter_upgrade_inputs, after_cell=inspect_upgrade_output)
        session.upgrade_host = 'nn1'
        run_notebook('O12h_Hadoop - Upgrade Hadoop node.ipynb', before_cell=enter_upgrade_inputs, after_cell=inspect_upgrade_output, instance='nn1')
        session.upgrade_host = 'nn2'
        run_notebook('O12h_Hadoop - Upgrade Hadoop node.ipynb', before_cell=enter_upgrade_inputs, after_cell=inspect_upgrade_output, instance='nn2')
        session.upgrade_host = 'dn1'
        run_notebook('O12h_Hadoop - Upgrade Hadoop node.ipynb', before_cell=enter_upgrade_inputs, after_cell=inspect_upgrade_output, instance='dn1')
        run_notebook('O12i_Hadoop - Upgrade HBase and Spark.ipynb', before_cell=enter_upgrade_inputs, after_cell=inspect_upgrade_output)
        run_notebook('T12d_Hadoop - Simple Spark script for Test.ipynb', before_cell=enter_post_upgrade_spark_inputs, instance='upgraded')
        run_notebook('O12j_Hadoop - Verify and finalize upgrade.ipynb', before_cell=enter_upgrade_inputs, after_cell=inspect_upgrade_output)
        session.upgrade_host = 'nn1'
        run_notebook('O12k_Hadoop - Update platform packages.ipynb', before_cell=enter_upgrade_inputs, after_cell=inspect_upgrade_output, instance='nn1')
        session.upgrade_host = 'nn2'
        run_notebook('O12k_Hadoop - Update platform packages.ipynb', before_cell=enter_upgrade_inputs, after_cell=inspect_upgrade_output, instance='nn2')
        session.upgrade_host = 'dn1'
        run_notebook('O12k_Hadoop - Update platform packages.ipynb', before_cell=enter_upgrade_inputs, after_cell=inspect_upgrade_output, instance='dn1')
        run_notebook('T12a_Hadoop - Confirm the services are alive - ZK,HDFS,YARN,HBase,Spark.ipynb', instance='upgraded')
        run_notebook('T12d_Hadoop - Simple Spark script for Test.ipynb', before_cell=enter_post_upgrade_spark_inputs, instance='platform-updated')
    finally:
        observer.finish()
    observer.verify(session)


def enter_post_upgrade_spark_inputs(session, controls, index, text):
    if text.startswith("hdfs_dataset_dir ="):
        suffix = 'platform-updated' if session.upgrade.get('platform_updates') else 'upgraded'
        controls.edit(index, f"hdfs_dataset_dir = '/dataset/iris-{suffix}'\nhadoop_client_dir = '~/iris-{suffix}'\n(hdfs_dataset_dir, hadoop_client_dir)\n",
                      'Select a fresh upload directory and retain the pre-upgrade input')
    if text.startswith('%%writefile') and 'SimpleApp.py' in text:
        suffix = 'platform-updated' if session.upgrade.get('platform_updates') else 'upgraded'
        controls.edit(index, text.replace('/dataset/iris/iris.data', f'/dataset/iris-{suffix}/iris.data'),
                      'Use the selected input directory in the sample application')
