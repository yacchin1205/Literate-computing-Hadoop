# Original Notebook HDFS construction through LC4RI

CI acts as the operator of the existing notebooks. It uses LC4RI's visible
Make a Copy, editor, Shift+Enter, Freeze/Unfreeze and Save controls. The original
cell sources are compared before copying and after execution; every working-copy
edit has a reason, before/after text, and an operation record. No alternative
installation notebook, generated deployment script, or direct Ansible installation
from the test harness is used.

## Exact scope

- D00: prerequisite checks; optional host-log/tree diagnostics are recorded.
- D10: all nonempty code cells. Enter the real three-VM host table in the file
  editor, merge the original cell's Inventory output through the file editor,
  and select archive locations/JVM memory budgets in generated group variables.
- D12: every nonempty code cell from the beginning through the HDFS Web UI link,
  stopping before the YARN installation heading. This builds the original HA
  topology: three ZooKeeper servers, three JournalNodes, two NameNodes/ZKFCs,
  and three DataNodes. The clone cell selects the exact tested fork commit.
- T12a: the original ZooKeeper/HDFS checks, stopping before YARN.
- Independent acceptance: one active/one standby, three live DataNodes, a
  three-block file written/read with SHA-256 equality and three replicas, fsck,
  and the real NameNode browser UI. The harness never constructs HDFS itself.

D11 depends on a private, redacted repository and is **not tested**. QEMU/cloud-init
supplies D12's documented OS prerequisites: Ubuntu 20.04 with cgroups v1, SSH/sudo,
Python/ACL, disabled IPv6, NTP configuration, hostname resolution and storage
mount-point directories. Java, cgroups packages, ZooKeeper and Hadoop are installed
by D12's original Ansible cells. The physical/KVM/GCE provisioning notebooks,
D90's private policy, YARN/HBase/Hive/Spark and migration are outside this scope.

The package mirror contains byte-identical Apache Archive tarballs, verified by
the repository SHA-512 values; it is an input/download service. The Ubuntu cloud
image and LC4RI image are pinned by hash/digest. Working-copy source edits are
restricted to documented inputs: archive URL and deployment repository/revision.
The generated topology remains unchanged when selecting JVM memory budgets.

## Evidence and execution

Run on an x86-64 Linux host with Docker, QEMU, cloud-image-utils, SSH and sudo:

```bash
python3 -m pip install -r tests/lc4ri/requirements.txt
python3 -m playwright install --with-deps chromium
bash tests/lc4ri/run.sh
```

The workflow uses disposable GitHub runners and never uploads SSH private keys.
`test-results/original-hdfs` contains original hashes, all work copies, per-cell
coverage (including the excluded later sections), input/operation records,
screenshots, browser trace and videos, inventory/group variables, package hashes,
LC logs, VM serial logs, service logs, and HDFS acceptance evidence. Original
Notebook source changes are product fixes committed separately from CI support.

Browser controls, evidence helpers and the QEMU lifecycle are adapted from
[yacchin1205/CoursewareHub-LC_deploy PR #1](https://github.com/yacchin1205/CoursewareHub-LC_deploy/pull/1),
commit `b22d9a0bad4df839a59c19ac56838e724390d788`; see that repository for attribution.
