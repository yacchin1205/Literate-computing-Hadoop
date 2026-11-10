"""Operations used by O12e. Run against a disposable D12 HDFS cluster."""
import json
import re
import shlex
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def wait_for(description, operation, timeout=180):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            if operation():
                return
        except (subprocess.SubprocessError, RuntimeError) as error:
            last_error = error
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {description}") from last_error


class HdfsUpgrade:
    def __init__(self, source, target, url, sha512, nameservice, namenodes, datanodes,
                 client, ssh_options):
        for version in (source, target):
            require(re.fullmatch(r"3\.\d+\.\d+", version), "Specify a Hadoop 3 release")
        require(tuple(map(int, target.split("."))) > tuple(map(int, source.split("."))),
                "Target release must be newer")
        require(url.startswith(("https://", "http://")), "Use an HTTP(S) archive URL")
        require(len(namenodes) == 2 and len(datanodes) >= 3,
                "Expected two NameNodes and at least three DataNodes")
        self.source, self.target, self.url, self.sha512 = source, target, url, sha512.lower()
        self.nameservice = nameservice
        self.namenodes, self.datanodes = dict(namenodes), dict(datanodes)
        self.client, self.ssh_options = client, list(ssh_options)
        self.new_home = f"/opt/hadoop/hadoop-{target}"
        self.phase = "new"
        self.stop = threading.Event()
        self.probe_thread = None
        self.samples = []
        identifier = uuid.uuid4().hex
        self.probe_path = f"/tmp/hdfs-rolling-upgrade-{identifier}"
        self.probe_log = Path(f"hdfs-rolling-upgrade-{identifier}.jsonl")
        self.payload = f"hdfs rolling upgrade {identifier}\n"

    def remote(self, host, *command, input_text=None, timeout=45, check=True):
        require(host and not host.startswith("-"), f"Invalid SSH target: {host!r}")
        return subprocess.run(
            ["ssh", *self.ssh_options, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
             host, shlex.join(command)],
            input=input_text, text=True, capture_output=True, check=check, timeout=timeout,
        )

    def hdfs(self, *args, host=None, input_text=None):
        return self.remote(host or self.client, "sudo", "-n", "-u", "hdfs",
                           "/opt/hadoop/current/bin/hdfs", *args, input_text=input_text).stdout.strip()

    def conf(self, key, host=None):
        return self.hdfs("getconf", "-confKey", key, host=host)

    def states(self):
        return {node: self.hdfs("haadmin", "-getServiceState", node) for node in self.namenodes}

    def pair(self):
        states = self.states()
        require(sorted(states.values()) == ["active", "standby"], f"Unexpected HA state: {states}")
        return tuple(next(node for node, state in states.items() if state == expected)
                     for expected in ("active", "standby"))

    def healthy(self, fully_replicated=False):
        report = self.hdfs("dfsadmin", "-report", "-live")
        require(f"Live datanodes ({len(self.datanodes)}):" in report, report)
        require(set(re.findall(r"^Hostname: (\S+)$", report, re.M)) == set(self.datanodes), report)
        require(len(re.findall(r"^Decommission Status\s*:\s*Normal$", report, re.M))
                == len(self.datanodes), report)
        for metric in ("Missing blocks", "Blocks with corrupt replicas", "Missing block groups",
                       "Block groups with corrupt internal blocks"):
            require(re.search(rf"^\s*{metric}: 0\s*$", report, re.M), report)
        if fully_replicated:
            for metric in ("Under replicated blocks", "Low redundancy block groups"):
                require(re.search(rf"^\s*{metric}: 0\s*$", report, re.M), report)
        require("Safe mode is ON" not in report, report)
        return True

    def service(self, host, action, role):
        return self.remote(host, "sudo", "-n", "systemctl", action, f"hadoop-hdfs-{role}")

    def preflight(self):
        require(self.phase == "new", self.phase)
        require(self.conf("fs.defaultFS") == f"hdfs://{self.nameservice}", "Use the logical HDFS URI")
        require(self.conf("dfs.nameservices") == self.nameservice, "Expected one nameservice")
        require(self.conf("hadoop.security.authentication") == "simple", "This notebook uses simple authentication")
        require(self.conf("dfs.ha.automatic-failover.enabled") == "true", "Enable automatic HA failover")
        require(set(self.conf(f"dfs.ha.namenodes.{self.nameservice}").split(",")) == set(self.namenodes),
                "NameNode IDs differ from the HDFS configuration")
        journals = self.conf("dfs.namenode.shared.edits.dir")
        require(journals.startswith("qjournal://") and journals.count(";") >= 2,
                "Expected a JournalNode quorum")
        self.dn_endpoints = {}
        for node, host in self.namenodes.items():
            address = self.conf(f"dfs.namenode.rpc-address.{self.nameservice}.{node}")
            self.check_host(host, address.rsplit(":", 1)[0], ("namenode", "zkfc"))
        for node, host in self.datanodes.items():
            self.check_host(host, node, ("datanode",))
            port = self.conf("dfs.datanode.ipc.address", host).rsplit(":", 1)[-1]
            require(port.isdecimal() and int(port) > 0, f"Invalid DataNode IPC port on {node}")
            self.dn_endpoints[node] = f"{node}:{port}"
            info = self.hdfs("dfsadmin", "-getDatanodeInfo", self.dn_endpoints[node])
            require(re.search(rf"\b{re.escape(self.source)}\b", info), info)
        self.check_version(self.client)
        require("no rolling upgrade in progress" in self.rolling("query"), "An upgrade is already in progress")
        self.healthy(fully_replicated=True)
        active, standby = self.pair()
        for node in self.namenodes:
            self.hdfs("haadmin", "-checkHealth", node)
        self.phase = "checked"
        print(f"Active={active}, Standby={standby}, DataNodes={list(self.datanodes)}")

    def check_version(self, host):
        actual = self.remote(host, "readlink", "-f", "/opt/hadoop/current").stdout.strip()
        require(actual == f"/opt/hadoop/hadoop-{self.source}", f"{host}: current points to {actual}")

    def check_host(self, host, expected_name, roles):
        actual = self.remote(host, "hostname", "-f").stdout.strip()
        require(actual == expected_name, f"{host}: expected {expected_name}, connected to {actual}")
        self.check_version(host)
        for role in roles:
            self.service(host, "is-active", role)
            unit = f"hadoop-hdfs-{role}"
            command = self.remote(host, "systemctl", "show", "--value", "-p", "DropInPaths", unit)
            require(not command.stdout.strip(), f"{host}: existing drop-ins for {unit}")
            definition = self.remote(host, "systemctl", "cat", unit).stdout
            require(f"ExecStart=/opt/hadoop/current/bin/hdfs --config /etc/hadoop/conf {role}" in definition,
                    f"{host}: unexpected {unit} ExecStart")

    def stage(self):
        require(self.phase in {"checked", "staging"}, self.phase)
        require(re.fullmatch(r"[a-f0-9]{128}", self.sha512), "Enter the archive SHA-512")
        self.phase = "staging"
        script = """set -euo pipefail
url=$1 checksum=$2 package=$3
destination="/opt/hadoop/$package"
if test -e "$destination"; then
    test "$(cat "$destination/.rolling-upgrade-sha512")" = "$checksum"
    exit
fi
work=$(mktemp -d /opt/hadoop/.rolling-upgrade.XXXXXXXX)
trap 'rm -rf -- "$work"' EXIT
curl --fail --location --proto '=http,https' --proto-redir '=https' --retry 2 -o "$work/package.tar.gz" "$url"
printf '%s  %s\\n' "$checksum" "$work/package.tar.gz" | sha512sum --check
tar --extract --gzip --no-same-owner --file "$work/package.tar.gz" --directory "$work"
test -x "$work/$package/bin/hdfs"
printf '%s\\n' "$checksum" > "$work/$package/.rolling-upgrade-sha512"
mv -T -- "$work/$package" "$destination"
"""
        for host in dict.fromkeys([*self.namenodes.values(), *self.datanodes.values()]):
            self.remote(host, "sudo", "-n", "bash", "-s", "--", self.url, self.sha512,
                        f"hadoop-{self.target}", input_text=script, timeout=900)
            version = self.remote(host, self.new_home + "/bin/hdfs", "--config",
                                  "/etc/hadoop/conf", "version").stdout
            require(version.splitlines()[0] == f"Hadoop {self.target}", version)
            print(f"{host}: {self.target} staged")
        self.phase = "staged"

    def start_probe(self):
        require(self.phase == "staged" and self.probe_thread is None, self.phase)
        self.hdfs("dfs", "-mkdir", "-p", self.probe_path)
        self.hdfs("dfs", "-Ddfs.replication=3", "-put", "-", self.probe_path + "/baseline",
                  input_text=self.payload)
        require(self.hdfs("dfs", "-cat", self.probe_path + "/baseline") == self.payload.strip(),
                "Baseline data mismatch")
        self.probe_thread = threading.Thread(target=self.probe_loop, daemon=True)
        self.probe_thread.start()
        wait_for("first successful read/write probe", lambda: any(s["ok"] for s in self.samples))
        print(f"Probe log: {self.probe_log}")

    def probe_loop(self):
        while not self.stop.is_set():
            start = time.monotonic()
            sample = {"time": datetime.now(timezone.utc).isoformat(), "phase": self.phase,
                      "started_monotonic": start}
            path = f"{self.probe_path}/sample-{len(self.samples):05d}"
            try:
                self.hdfs("dfs", "-Ddfs.replication=2", "-put", "-", path, input_text=self.payload)
                require(self.hdfs("dfs", "-cat", path) == self.payload.strip(), "Probe data mismatch")
                sample["ok"] = True
            except (RuntimeError, subprocess.SubprocessError) as error:
                sample.update(ok=False, error=str(error), stderr=getattr(error, "stderr", None))
            sample["finished_monotonic"] = time.monotonic()
            sample["seconds"] = round(sample["finished_monotonic"] - start, 3)
            with self.probe_log.open("a", encoding="utf-8") as log:
                log.write(json.dumps(sample) + "\n")
            self.samples.append(sample)
            self.stop.wait(1)

    def probe_ok(self):
        require(self.probe_thread is not None and self.probe_thread.is_alive(), "Read/write probe stopped")
        require(self.samples and all(sample["ok"] for sample in self.samples),
                f"Read/write probe failed. See {self.probe_log}")

    def stop_probe(self):
        self.stop.set()
        if self.probe_thread is not None:
            self.probe_thread.join(timeout=95)
            require(not self.probe_thread.is_alive(), "Probe is still finishing a remote command")

    def rolling(self, action):
        return self.hdfs("dfsadmin", "-rollingUpgrade", action)

    def prepare(self):
        require(self.phase == "staged", self.phase)
        self.probe_ok()
        self.active, self.standby = self.pair()
        self.phase = "preparing"
        print(self.rolling("prepare"))
        wait_for("rollback image", lambda: "Proceed with rolling upgrade" in self.rolling("query"), 300)
        self.phase = "prepared"

    def write_unit(self, host, role, rolling=False):
        unit = f"hadoop-hdfs-{role}.service"
        directory = f"/etc/systemd/system/{unit}.d"
        args = " -rollingUpgrade started" if rolling else ""
        # The original EnvironmentFile sets HADOOP_HOME to the shared current link.
        content = (f"[Service]\nExecStart=\nExecStart=/usr/bin/env HADOOP_HOME={self.new_home} "
                   f"{self.new_home}/bin/hdfs --config /etc/hadoop/conf {role}{args}\n")
        self.remote(host, "sudo", "-n", "install", "-d", "-m", "0755", directory)
        self.remote(host, "sudo", "-n", "tee", directory + "/90-rolling-upgrade.conf", input_text=content)

    def reload(self, host):
        self.remote(host, "sudo", "-n", "systemctl", "daemon-reload")

    def verify_process(self, host, role):
        pid = self.remote(host, "systemctl", "show", "-p", "MainPID", "--value",
                          f"hadoop-hdfs-{role}").stdout.strip()
        require(pid.isdecimal() and int(pid) > 0, f"{host}: {role} has no MainPID")
        args = self.remote(host, "sudo", "-n", "cat", f"/proc/{pid}/cmdline").stdout
        require(f"{self.new_home}/share/hadoop/hdfs" in args, f"{host}: {role} is not running {self.target}")
        return True

    def update_namenode(self, node, peer):
        self.probe_ok()
        require(self.pair() == (peer, node), f"{node} is not standby with {peer} active")
        host = self.namenodes[node]
        for role in ("zkfc", "namenode"):
            self.service(host, "stop", role)
            self.write_unit(host, role, rolling=(role == "namenode"))
        self.reload(host)
        for role in ("namenode", "zkfc"):
            self.service(host, "start", role)
        wait_for(f"{node} standby", lambda: self.pair() == (peer, node))
        for role in ("namenode", "zkfc"):
            wait_for(f"{node} {role} at {self.target}", lambda: self.verify_process(host, role))
        self.hdfs("haadmin", "-checkHealth", node)
        self.probe_ok()
        print(f"{node}: standby at {self.target}")

    def update_standby(self):
        require(self.phase == "prepared", self.phase)
        self.phase = "updating standby"
        self.update_namenode(self.standby, self.active)
        self.phase = "standby updated"

    def failover(self):
        require(self.phase == "standby updated", self.phase)
        self.probe_ok()
        require(self.pair() == (self.active, self.standby), "HA roles changed before failover")
        self.phase = "failover"
        self.failover_start = time.monotonic()
        print(self.hdfs("haadmin", "-failover", self.active, self.standby))
        wait_for("HA role switch", lambda: self.pair() == (self.standby, self.active))
        self.failover_finish = time.monotonic()
        print(f"{self.standby}: active, failover verified in {self.failover_finish - self.failover_start:.2f}s")
        self.probe_ok()
        self.phase = "failed over"

    def update_former_active(self):
        require(self.phase == "failed over", self.phase)
        self.phase = "updating former active"
        self.update_namenode(self.active, self.standby)
        self.phase = "namenodes updated"

    def update_datanodes(self):
        require(self.phase == "namenodes updated", self.phase)
        for node, host in self.datanodes.items():
            self.phase = f"updating DataNode {node}"
            self.probe_ok()
            self.healthy(fully_replicated=True)
            endpoint = self.dn_endpoints[node]
            self.hdfs("dfsadmin", "-shutdownDatanode", endpoint, "upgrade")
            wait_for(f"{node} service to stop",
                     lambda: self.remote(host, "systemctl", "is-active", "hadoop-hdfs-datanode",
                                         check=False).stdout.strip() in {"inactive", "failed"})
            self.write_unit(host, "datanode")
            self.reload(host)
            self.service(host, "start", "datanode")
            wait_for(f"{node} to serve DataNode RPC at {self.target}",
                     lambda: bool(re.search(rf"\b{re.escape(self.target)}\b",
                                            self.hdfs("dfsadmin", "-getDatanodeInfo", endpoint))))
            self.verify_process(host, "datanode")
            wait_for("all DataNodes and healthy replication", lambda: self.healthy(fully_replicated=True))
            self.probe_ok()
            print(f"{node}: DataNode at {self.target}")
        self.phase = "datanodes updated"

    def verify(self):
        require(self.phase == "datanodes updated", self.phase)
        require(self.pair() == (self.standby, self.active), "Unexpected HA roles after upgrade")
        self.healthy(fully_replicated=True)
        require(self.hdfs("dfs", "-cat", self.probe_path + "/baseline") == self.payload.strip(),
                "Pre-upgrade data mismatch")
        self.probe_ok()
        self.stop_probe()
        report = self.hdfs("fsck", self.probe_path, "-files", "-blocks")
        require("Status: HEALTHY" in report, report)
        require(all(sample["ok"] for sample in self.samples), f"Probe failed. See {self.probe_log}")
        self.overlapping_probes = sum(
            sample["started_monotonic"] <= self.failover_finish
            and sample["finished_monotonic"] >= self.failover_start
            for sample in self.samples
        )
        require(self.overlapping_probes > 0, "No read/write sample overlapped the failover")
        print(f"{len(self.samples)} successful read/write probes, max "
              f"{max(s['seconds'] for s in self.samples):.3f}s. Evidence: {self.probe_log}")
        self.phase = "verified"

    def finalize(self):
        require(self.phase == "verified", self.phase)
        require(self.pair() == (self.standby, self.active), "HA roles changed after verification")
        self.healthy(fully_replicated=True)
        require("Proceed with rolling upgrade" in self.rolling("query"), "Upgrade is not ready to finalize")
        self.phase = "finalizing"
        for host in self.namenodes.values():
            self.write_unit(host, "namenode")
            self.reload(host)
        print(self.rolling("finalize"))
        require("no rolling upgrade in progress" in self.rolling("query"), "Finalize did not complete")
        self.phase = "finalized"

    def evidence(self):
        require(self.phase in {"verified", "finalized"}, self.phase)
        return dict(source=self.source, target=self.target, phase=self.phase,
                    original_active=self.active, original_standby=self.standby,
                    final_states=self.states(), datanodes=list(self.datanodes),
                    failover_seconds=round(self.failover_finish - self.failover_start, 3),
                    probes_overlapping_failover=self.overlapping_probes,
                    rolling_status=self.rolling("query"), samples=list(self.samples))
