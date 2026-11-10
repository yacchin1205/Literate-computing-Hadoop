# QEMU notebook integration CI

The first scenario installs the historical ZooKeeper **3.6.2** baseline in one
Ubuntu 22.04 QEMU/KVM guest using this repository's install/start/stop playbooks.
The Python 3 companion notebook checks the actual server version, writes a
persistent znode, and reads it after service stop/start and a guest reboot.
The guest's disk is preserved through every phase. This tests standalone
ZooKeeper; it does not test an ensemble, Hadoop, Kerberos, or an upgrade yet.

The workflow runs on pull requests, manual dispatch, and pushes to
`ci/qemu-notebook`. `/dev/kvm` is required and checked explicitly. Nested
virtualization is not guaranteed by GitHub; a runner without KVM fails instead
of quietly falling back to very slow emulation.

## Run locally on an x86-64 Linux KVM host

Install `qemu-system-x86`, `qemu-utils`, `cloud-image-utils`, `curl`, OpenSSH,
Python 3.11 and venv support. From the repository root:

```bash
python3.11 -m venv .venv-ci
. .venv-ci/bin/activate
pip install -r ci/requirements.txt
python -m ipykernel install --user --name python3
export CI_STATE="$PWD/.ci-state"
export CI_ARTIFACTS="$PWD/.ci-artifacts"
mkdir -p "$CI_ARTIFACTS"
export ANSIBLE_LOG_PATH="$CI_ARTIFACTS/ansible.log"
bash ci/qemu.sh start
python ci/run-notebook.py
bash ci/qemu.sh collect
bash ci/qemu.sh stop
```

Always collect and stop after a failure too. Use an empty `.ci-state` directory
for each new run. Ports 2222 and 22181 are bound to loopback for guest SSH and
ZooKeeper respectively. The generated guest SSH host key is pinned before boot.
Only `.ci-artifacts` is uploaded: never upload `.ci-state`, which contains keys.

Artifacts include the executed notebook (including partial output on failure),
Ansible log, guest journal, serial console, resolved controller package versions,
and the Ubuntu image checksum. Notebook commands use `subprocess.run(check=True)`
so failed Ansible commands fail CI. Real client reads check service/data health.
The Ubuntu release image and apt repositories track updates; this is not yet a
fully frozen OS fixture. The image is checked against Canonical's HTTPS checksum
manifest, and the resolved checksum is recorded. ZooKeeper's historical tarball
uses Apache Archive and a fixed SHA-512 checksum.

## Migration direction

Keep the environment lifecycle separate from the scenario notebook. Subsequent
scenarios can use the same guest/disk and explicit baseline/target variable files:

1. Install a pinned old release and verify its version.
2. Create representative persistent data and record expected values/checksums.
3. Stop services using the supported upgrade procedure.
4. Install a pinned target release without recreating data directories or disks.
5. Verify the target version, read the old data, and write/read new data.
6. For Hadoop, check HDFS contents, HA state, and a real YARN job, and distinguish
   upgrade, finalize, and rollback where the selected version pair supports them.

Successful independent fresh installs are not evidence of migration compatibility.
A ZooKeeper restart is also not a migration test. Version pairs, data fixtures,
resource sizing, and rollback expectations must be chosen per scenario.

The legacy D12/T12a notebooks include interactive choices, Python 2 code, and HDP
paths. This initial companion notebook exercises their ZooKeeper intent through
the current role; it does not claim to execute those original notebooks unchanged.
The ZooKeeper role no longer depends on Hadoop configuration/install tasks:
its own install tasks already create the `hadoop` group, and its configuration
uses Java and ZooKeeper variables only. The unused HDP daemon path was removed.
Only task imports on this scenario's execution path were updated for Ansible Core.
