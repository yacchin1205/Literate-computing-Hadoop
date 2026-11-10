# Original Notebook HDFS, YARN and Spark construction through LC4RI

CI acts as the operator of the existing notebooks. It uses LC4RI's visible
Make a Copy, editor, Clear Output, Shift+Enter, Freeze/Unfreeze and Save controls. The original
cell sources are compared before copying and after execution; every working-copy
edit has a reason, before/after text, and an operation record. No alternative
installation notebook, generated deployment script, or direct Ansible installation
from the test harness is used.

## Exact scope

- D00: prerequisite checks; optional host-log/tree diagnostics are recorded.
- D10: all nonempty code cells. Enter the real three-VM host table in the file
  editor, merge the original cell's Inventory output through the file editor,
  and select archive locations/JVM memory budgets in generated group variables.
- D12: all nonempty code cells through HDFS and YARN, then the existing Spark
  installation/history section and disk check. Tez/HBase/Hive sections and later
  duplicate service examples are explicitly excluded. The clone cell selects the
  exact tested fork commit. Three ZooKeeper/JournalNodes/DataNodes, two
  NameNodes/ZKFCs/ResourceManagers, three NodeManagers and the history services
  are built by the original cells.
- T12a: ZooKeeper/HDFS/YARN/Spark service checks; HBase is explicitly excluded.
- T12d: the full original Spark example: download Iris, upload it to HDFS,
  submit the Python application in YARN cluster mode, and retrieve its actual
  application logs. The example persists its counts back to HDFS.
- Independent acceptance: HDFS HA, three live DataNodes, a three-block file
  written/read with matching SHA-256, three replicas and fsck; then read T12d's
  HDFS input and result, verify 150 rows and 50 of each species, YARN HA, three
  RUNNING NodeManagers, a completed SUCCEEDED SPARK application with consumed
  container resources, driver logs, and the actual NameNode, YARN application,
  and Spark History Server browser pages. No acceptance command installs services.

D11 depends on a private, redacted repository and is **not tested**. QEMU/cloud-init
supplies D12's documented OS prerequisites: Ubuntu 20.04 with cgroups v1, SSH/sudo,
Python/ACL, disabled IPv6, NTP configuration, hostname resolution and storage
mount-point directories. Java, cgroups packages, ZooKeeper, Hadoop and Spark are installed
by D12's original Ansible cells. The physical/KVM/GCE provisioning notebooks,
D90's private policy, Tez/HBase/Hive and migration are outside this scope.

Working copies have historical outputs cleared through the UI before execution.
The package mirror contains byte-identical historical Apache tarballs, verified by
the repository SHA-512 values; it is an input/download service. The Hadoop and Spark
archives is retrieved from Huawei Cloud's Apache mirror because Apache Archive
was rate-limited to about 120 KiB/s in the first Actions run. The manifest records
the retrieval URL, and the original repository SHA-512 must match. The Ubuntu cloud
image and LC4RI image are pinned by hash/digest. Working-copy source edits are
restricted to documented inputs: archive URL and deployment repository/revision.
The generated topology remains unchanged when selecting JVM memory budgets.
VM RAM is 4096/4096/3072 MiB; each NodeManager offers 1024 MiB and one
vcore. The Spark driver and two executors each request 512 MiB plus 384 MiB
overhead, rounded to one 1024 MiB YARN container per node.

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
LC logs, VM serial logs, service logs, and HDFS/YARN/Spark acceptance evidence. Original
Notebook source changes are product fixes committed separately from CI support.
A second, small `original-notebook-results` artifact contains the same result JSON,
working copies and screenshots without the large browser trace/videos, so a slow
artifact download does not prevent reviewing the actual outcomes.

Browser controls, evidence helpers and the QEMU lifecycle are adapted from
[yacchin1205/CoursewareHub-LC_deploy PR #1](https://github.com/yacchin1205/CoursewareHub-LC_deploy/pull/1),
commit `b22d9a0bad4df839a59c19ac56838e724390d788`; see that repository for attribution.
