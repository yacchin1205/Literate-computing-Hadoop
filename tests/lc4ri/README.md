# Notebook integration tests

The `basic` and `security` jobs operate the scenario in [scenario.py](scenario.py)
on disposable QEMU VMs. The scenario lists the notebooks in execution order and
supplies operator inputs. Each notebook runs all nonempty code cells. Cell errors
fail the job, and service validation follows the notebook procedures.
T12c creates an HBase table, writes the Iris dataset, reads it back, and deletes
the table. The security variant performs these operations as the HBase administrator.

The controller uses `niicloudoperation/notebook:feature-lab` and each notebook's declared
kernel. Its image digest and installed package versions are saved with the
results. Software archives and checksums come from the repository's deployment
defaults and are served through a local mirror backed by Apache Archive.
Archived packages use parallel range downloads followed by SHA-512 verification.
The [guest image](guests/guest-image.env) supplies the cgroups v1 environment
expected by the deployment roles. In Kerberos variants, D11a creates a root CA and
issues the guest HTTPS certificates before D12. Kerberos service and client
prerequisites remain part of the deployment procedure.

Run on a disposable x86-64 Linux host with Docker, QEMU, cloud-image-utils,
SSH, sudo, FFmpeg, DejaVu and Noto CJK fonts, using the prerequisites in
[the workflow](../../.github/workflows/notebook-integration.yml):

```bash
python3 -m pip install -r tests/lc4ri/requirements.txt
python3 -m playwright install --with-deps chromium
install -Dm644 tests/lc4ri/fonts.conf ~/.config/fontconfig/fonts.conf
HADOOP_SECURITY_MODE=simple bash tests/lc4ri/run.sh
HADOOP_SECURITY_MODE=kerberos bash tests/lc4ri/run.sh
HADOOP_SECURITY_MODE=kerberos HADOOP_TEST_SCENARIO=ca-renewal bash tests/lc4ri/run.sh
```

Results in `test-results/notebook-integration` are uploaded separately for each
variant:

| Artifact | Contents |
| --- | --- |
| `notebook-results-*` | Executed notebooks, JSON reports, screenshots and the video manifest |
| `notebook-logs-*` | Service logs, runtime diagnostics, inventory and group variables |
| `notebook-trace-*` | Browser trace |
| `notebook-video-*-D00` (and other notebook IDs) | Notebook chapter MP4 and its source page recordings |
| `notebook-video-*-combined` | Combined MP4 in notebook execution order |

Each chapter starts with a two-second notebook title. File-editor operations
appear where they occurred in the notebook procedure; background page recordings
are not played simultaneously or appended out of order. Playback is at normal
speed. Original page recordings remain available in the per-notebook artifacts.

On failure, each guest's `/var/log/hadoop-yarn/userlogs` directory is archived
before cleanup and included as `*-yarn-userlogs.tar.gz` in the logs artifact.
Video rendering also runs after a notebook failure, preserving the attempted
procedure for review.

Browser and QEMU helpers are adapted from
[CoursewareHub-LC_deploy](https://github.com/yacchin1205/CoursewareHub-LC_deploy/pull/1).

The `ca-renewal` matrix runs O12l against the deployed cluster. It introduces
old/new CA trust, reissues host keys and certificates, and removes the old CA.
Independent TLS handshakes verify all Hadoop HTTPS endpoints before and after
renewal. Java truststores are checked at both trust transitions, host public keys
must change, and the old CA must fail to validate the updated endpoints. T12a,
T12c and T12d then check services, HBase operations and a YARN Spark job.
Public CA certificates and acceptance reports are saved with the results. Private
keys remain in the disposable controller outside the notebook artifact directory.


The `upgrade-basic` and `upgrade-security` matrices deploy the configured releases with D12,
then execute the O12e–O12k procedures: prepare and pause application use, upgrade
ZooKeeper one peer at a time, roll the NameNodes, switch each host's shared Hadoop
release, upgrade HBase/Spark, verify data and finalize, then update OS-provided
Java/cgroups/Kerberos packages and restart their consumers one host at a time. jsvc is rebuilt and switched
with each Hadoop host. OS packages whose candidate equals the installed version
remain unchanged; this does not test an OS distribution migration.

Each repeated host procedure has its own working notebook and coverage. The test
enters operator inputs and edits group_vars through the notebook UI. Every nonempty
code cell runs. Existing service definitions and shared release pointers are retained.
New playbooks stage packages and switch the existing release pointers.

An independent old-version HDFS client records writes and reads through ZooKeeper,
HDFS, application and platform-package updates, including planned NameNode failovers. Acceptance
checks preserve pre-upgrade HDFS data, verify versions and replication, and require
finalization and a return to the existing systemd services. Observation continues
through the host-by-host platform service restarts and final workload checks.
HBase is tested for pre-upgrade data retention and new writes. MapReduce and Spark
run real YARN jobs after the shared Hadoop release changes; T12a and T12d run again
after platform service restarts. Full outputs, working notebooks and source hashes are saved.
