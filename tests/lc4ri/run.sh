#!/usr/bin/env bash
set -Eeuo pipefail
result_dir="${GITHUB_WORKSPACE:-$PWD}/test-results/original-hdfs"
mkdir -p "$result_dir"
exec > >(tee "$result_dir/run.log") 2>&1
source tests/lc4ri/guests/qemu-guests.sh
bridge=hdfs-e2e-br0
subnet=172.29.88.0/24
gateway=172.29.88.1
controller=hdfs-lc4ri-controller
mirror_pid=''
cleanup() {
  result=$?
  trap - EXIT
  set +e
  docker logs "$controller" > "$result_dir/controller.log" 2>&1
  docker cp "$controller:/home/jovyan/work/.log" "$result_dir/lc-logs"
  docker cp "$controller:/home/jovyan/work/inventory" "$result_dir/"
  docker cp "$controller:/home/jovyan/work/group_vars" "$result_dir/"
  for address in "${qemu_addresses[@]}"; do
    timeout 20s ssh -F "$private_dir/ssh_config" "ansible@$address" \
      'sudo journalctl --no-pager -n 400; sudo tail -n 200 /var/log/hadoop-hdfs/*.log; sudo tail -n 100 /var/log/zookeeper/*' \
      > "$result_dir/$address-services.log" 2>&1
  done
  docker rm -f "$controller"
  [[ -z "$mirror_pid" ]] || kill "$mirror_pid"
  qemu_host_diagnostics
  qemu_cleanup
  sudo chown -R "$(id -u):$(id -g)" "$result_dir"
  exit "$result"
}
trap cleanup EXIT
qemu_boot_guests 'nn1:172.29.88.10:3072:20G' 'nn2:172.29.88.11:3072:20G' 'dn1:172.29.88.12:2048:20G'
# Supply D12's documented OS/network/storage prerequisites, never install Hadoop here.
for address in "${qemu_addresses[@]}"; do
  ssh -F "$private_dir/ssh_config" "ansible@$address" \
    'set -e; sudo apt-get update && sudo apt-get install -y python3 acl rsyslog tree lsb-release wget; sudo mkdir -p /hadoop/data /hadoop/data01; sudo chmod 0755 /hadoop /hadoop/data /hadoop/data01; timedatectl status; cat /etc/hosts'
done
# A byte-identical local download mirror avoids fetching the 377 MiB tarball per VM.
package_cache="${HDFS_E2E_PACKAGE_CACHE:-$private_dir/packages}"
mkdir -p "$package_cache"
python3 - "$package_cache" "$result_dir" <<'PY'
import hashlib, json, pathlib, subprocess, sys, yaml
folder, results = map(pathlib.Path, sys.argv[1:])
packages = [
 ('hadoop', 'https://repo.huaweicloud.com/apache/hadoop/common/hadoop-3.2.2/hadoop-3.2.2.tar.gz', 'hadoop'),
 ('zookeeper', 'https://archive.apache.org/dist/zookeeper/zookeeper-3.6.2/apache-zookeeper-3.6.2-bin.tar.gz', 'zookeeper_server'),
 ('jsvc', 'https://archive.apache.org/dist/commons/daemon/source/commons-daemon-1.2.4-src.tar.gz', 'hadoop'),
]
manifest=[]
for key, url, defaults in packages:
 values=yaml.safe_load(pathlib.Path('playbooks/group_vars/all',defaults).read_text())
 filename=url.rsplit('/',1)[-1]
 expected=values[key+'_release_checksum']
 target=folder/filename
 digest=hashlib.sha512(target.read_bytes()).hexdigest() if target.is_file() else None
 if 'sha512:'+str(digest) != expected:
  print('Download prerequisite archive:',url,flush=True)
  subprocess.run(['curl','--fail','--location','--retry','3','--connect-timeout','20',url,'-o',str(target)],check=True,timeout=900)
 digest=hashlib.sha512(target.read_bytes()).hexdigest()
 assert 'sha512:'+digest == values[key+'_release_checksum'], (key,digest)
 manifest.append(dict(package=key,url=url,filename=filename,sha512=digest))
(results/'package-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
(folder/'.verified').write_text('All archive SHA-512 checks passed\n')
PY
python3 -m http.server 18555 --bind "$gateway" --directory "$package_cache" > "$result_dir/mirror.log" 2>&1 &
mirror_pid=$!
# The original D12 clone cell will clone this exact checked-out fork revision.
git clone --no-hardlinks --bare "$PWD" "$private_dir/source.git"
chmod -R a+rX "$private_dir/source.git"
docker build -t hdfs-lc4ri-controller tests/lc4ri/controller
export LC4RI_TEST_TOKEN
LC4RI_TEST_TOKEN=$(python3 -c 'import secrets; print(secrets.token_hex(24))')
export LC4RI_TEST_REVISION
LC4RI_TEST_REVISION=$(git rev-parse HEAD)
docker run -d --name "$controller" --network host \
  -v "$private_dir/source.git:/opt/test-source.git:ro" \
  -e ANSIBLE_CONFIG=/home/jovyan/work/ansible.cfg \
  -e ANSIBLE_NOCOLOR=1 -e ANSIBLE_DEPRECATION_WARNINGS=False \
  --entrypoint jupyter hdfs-lc4ri-controller notebook \
  --ip=0.0.0.0 --port=18888 --NotebookApp.token="$LC4RI_TEST_TOKEN" \
  --NotebookApp.notebook_dir=/home/jovyan/work --NotebookApp.default_url=/tree \
  --NotebookApp.kernel_manager_class=notebook.services.kernels.kernelmanager.AsyncMappingKernelManager
docker cp "$private_dir/id_ed25519" "$controller:/home/jovyan/.ssh/id_rsa"
docker cp "$private_dir/id_ed25519.pub" "$controller:/home/jovyan/.ssh/id_rsa.pub"
docker cp "$private_dir/known_hosts" "$controller:/home/jovyan/.ssh/known_hosts"
docker cp scripts "$controller:/home/jovyan/work/scripts"
docker cp hosts.csv "$controller:/home/jovyan/work/hosts.csv"
docker exec -u root "$controller" sh -c 'chmod 0600 /home/jovyan/.ssh/*; chmod 0644 /home/jovyan/.ssh/*.pub; chown -R 1000:100 /home/jovyan/work /home/jovyan/.ssh; printf "Host *\n  BatchMode yes\n  StrictHostKeyChecking yes\n  IdentitiesOnly yes\n  IdentityFile ~/.ssh/id_rsa\n" > /home/jovyan/.ssh/config; chown 1000:100 /home/jovyan/.ssh/config; chmod 0600 /home/jovyan/.ssh/config'
docker image inspect hdfs-lc4ri-controller --format '{{json .RepoDigests}}' > "$result_dir/controller-image.json"
python3 tests/lc4ri/notebook_ui.py --result-dir "$result_dir"
