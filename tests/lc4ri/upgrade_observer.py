"""Observe client reads/writes independently of the rolling-upgrade notebooks."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import threading
import time
import uuid


class UpgradeObserver:
    def __init__(self, results):
        self.results = results
        self.ssh = ['ssh', *shlex.split(os.environ['ANSIBLE_SSH_ARGS']), 'ansible@172.29.88.10']
        self.hdfs = 'sudo -n -u hdfs /opt/hadoop/hadoop-3.2.2/bin/hdfs'
        self.path = '/tmp/lc4ri-upgrade-' + uuid.uuid4().hex
        self.samples = []
        self.stop = threading.Event()
        self.thread = None
        self.lock = threading.Lock()

    def command(self, script, timeout=120):
        result = subprocess.run([*self.ssh, 'bash -seuo pipefail'], input=script,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, timeout=timeout)
        with self.lock:
            with (self.results / 'upgrade-client.log').open('a') as log:
                log.write(script + '\n' + result.stdout)
        result.check_returncode()
        return result.stdout

    def begin(self):
        self.command(f'{self.hdfs} version\n{self.hdfs} dfs -mkdir -p {self.path}\n'
                     f'head -c 3145728 /dev/zero | {self.hdfs} dfs -Ddfs.replication=3 -put - {self.path}/baseline\n'
                     f'{self.hdfs} dfs -cat {self.path}/baseline | sha256sum\n')
        self.thread = threading.Thread(target=self.observe, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 120
        while not self.samples and time.monotonic() < deadline:
            time.sleep(0.5)
        assert self.samples and self.samples[0]['ok'], self.samples

    def observe(self):
        while not self.stop.is_set():
            index = len(self.samples)
            value = f'LC4RI upgrade sample {index}'
            sample = dict(started=time.time(), index=index)
            try:
                output = self.command(f"printf '%s\\n' '{value}' | {self.hdfs} dfs -Ddfs.replication=2 -put - {self.path}/sample-{index}\n"
                                      f'{self.hdfs} dfs -cat {self.path}/sample-{index}\n')
                assert value in output, output
                sample['ok'] = True
            except Exception as error:
                sample.update(ok=False, error=str(error), output=getattr(error, 'stdout', None))
            sample['finished'] = time.time()
            self.samples.append(sample)
            (self.results / 'upgrade-client-samples.json').write_text(json.dumps(self.samples, indent=2) + '\n')
            self.stop.wait(0.1)

    def finish(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=130)
            assert not self.thread.is_alive(), 'Client observation has not stopped'

    def verify(self, session):
        checkpoint = dict(session.upgrade)
        assert self.samples and all(s['ok'] for s in self.samples), self.samples
        overlaps = [[s for s in self.samples if s['started'] <= window['finished']
                     and s['finished'] >= window['started']] for window in checkpoint['failovers']]
        assert overlaps and all(overlaps), 'No client operation overlapped a planned failover'
        overlap = {s['index'] for samples in overlaps for s in samples}
        digest = hashlib.sha256(bytes(3145728)).hexdigest()
        original = self.command(f'{self.hdfs} dfs -cat {self.path}/baseline | sha256sum\n')
        assert digest in original, original
        fsck = self.command(f'{self.hdfs} fsck {self.path} -files -blocks\n')
        assert 'Status: HEALTHY' in fsck, fsck
        status = self.command(f'{self.hdfs} dfsadmin -rollingUpgrade query\n')
        assert 'no rolling upgrade in progress' in status.lower(), status
        states = self.command(f'{self.hdfs} haadmin -getAllServiceState\n')
        assert sorted(re.findall(r'nn[12]:\d+\s+(active|standby)', states)) == ['active', 'standby'], states
        versions = {}
        for name in ('nn1', 'nn2'):
            if checkpoint['authentication'] == 'kerberos':
                url = f"https://{name}:{checkpoint['namenode_https_port']}/jmx"
                authentication = f"--negotiate -u : --cacert {shlex.quote(checkpoint['namenode_ca_file'])}"
            else:
                url = f"http://{name}:{checkpoint['namenode_http_port']}/jmx"
                authentication = ''
            raw = self.command(f"sudo -n -u hdfs curl --fail --no-progress-meter {authentication} --get --data-urlencode 'qry=Hadoop:service=NameNode,name=NameNodeInfo' {url}\n")
            info = json.loads(raw)['beans'][0]
            assert info['Version'].startswith(checkpoint['target_version']), info
            versions[name] = info['Version']
        for name in ('nn1', 'nn2', 'dn1'):
            raw = self.command(f'{self.hdfs} dfsadmin -getDatanodeInfo {name}:9867\n')
            assert checkpoint['target_version'] in raw, raw
        report = self.command(f'{self.hdfs} dfsadmin -report\n')
        assert 'Live datanodes (3):' in report, report
        for label in ('Missing blocks', 'Blocks with corrupt replicas', 'Under replicated blocks'):
            assert re.search(r'^\s*' + label + r': 0\s*$', report, re.M), report
        checkpoint.update(existing_data_sha256=digest, samples=self.samples,
                          samples_overlapping_failover=len(overlap), namenode_versions=versions)
        checkpoint['component_evidence'] = self.verify_components(checkpoint)
        (self.results / 'upgrade-acceptance.json').write_text(json.dumps(checkpoint, indent=2) + '\n')

    def remote_command(self, name, script):
        address = {'nn1': '172.29.88.10', 'nn2': '172.29.88.11', 'dn1': '172.29.88.12'}[name]
        result = subprocess.run([*self.ssh[:-1], 'ansible@' + address, 'bash -seuo pipefail'],
                                input=script, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        with (self.results / 'upgrade-components.log').open('a') as log:
            log.write(name + ': ' + script + '\n' + result.stdout)
        result.check_returncode()
        return result.stdout

    def verify_components(self, checkpoint):
        evidence = {}
        for name in ('nn1', 'nn2', 'dn1'):
            raw = self.remote_command(name, 'readlink -f /opt/hadoop/current\nhadoop version\n'
                                      'sudo systemctl show --property=ActiveState --property=MainPID hadoop-yarn-nodemanager hadoop-hdfs-journalnode\n'
                                      'curl --fail --no-progress-meter http://localhost:8080/commands/srvr\n'
                                      'java_home=$(dirname "$(dirname "$(readlink -f "$(command -v java)")")")\n'
                                      '/usr/bin/jsvc -home "$java_home" -help\n')
            assert checkpoint['target_version'] in raw, raw
            assert checkpoint['zookeeper_version'] in raw, raw
            assert checkpoint['jsvc_version'] in raw, raw
            assert 'ActiveState=active' in raw and 'MainPID=0\n' not in raw, raw
            evidence[name] = raw
        raw = self.remote_command('nn1', 'hbase version\nspark-submit --version\n'
                                  'sudo systemctl is-active hbase-master hbase-regionserver spark-history-server '
                                  'hadoop-mapreduce-historyserver hadoop-yarn-timelineserver\n')
        assert checkpoint['hbase_version'] in raw and checkpoint['spark_version'] in raw, raw
        evidence['applications'] = raw
        return evidence
