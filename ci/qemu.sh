#!/usr/bin/env bash
# One disposable guest. Never upload CI_STATE: it contains the SSH private key.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${CI_STATE:=$PWD/.ci-state}"
: "${CI_ARTIFACTS:=$PWD/.ci-artifacts}"
export CI_STATE CI_ARTIFACTS
mkdir -p "$CI_STATE" "$CI_ARTIFACTS"
ssh_options=(-i "$CI_STATE/id_ed25519" -p 2222 -o BatchMode=yes
  -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$CI_STATE/known_hosts"
  -o ConnectTimeout=5)
case "${1:-}" in
  start)
    # Fail explicitly: silently using software emulation masks runner regressions.
    test -c /dev/kvm || { echo '/dev/kvm is unavailable on this runner' >&2; exit 1; }
    test ! -e "$CI_STATE/qemu.pid"
    sudo chmod a+rw /dev/kvm
    image_base=https://cloud-images.ubuntu.com/releases/jammy/release
    image_name=ubuntu-22.04-server-cloudimg-amd64.img
    curl --fail --location --retry 3 "$image_base/SHA256SUMS" -o "$CI_STATE/SHA256SUMS"
    curl --fail --location --retry 3 "$image_base/$image_name" -o "$CI_STATE/$image_name"
    (cd "$CI_STATE"; awk -v name="$image_name" '$2 == "*"name || $2 == name {print}' SHA256SUMS > image.sha256
      test -s image.sha256; sha256sum -c image.sha256)
    cp "$CI_STATE/image.sha256" "$CI_ARTIFACTS/image.sha256"
    ssh-keygen -q -t ed25519 -N '' -f "$CI_STATE/id_ed25519"
    # Pin the guest host key before its first boot; no trust-on-first-use race.
    ssh-keygen -q -t ed25519 -N '' -f "$CI_STATE/ssh_host_ed25519_key"
    printf '[127.0.0.1]:2222 %s\n' "$(cat "$CI_STATE/ssh_host_ed25519_key.pub")" > "$CI_STATE/known_hosts"
    python3 - <<'PY'
import os
from pathlib import Path
import yaml
s = Path(os.environ['CI_STATE'])
config = {
    'hostname': 'notebook-ci', 'manage_etc_hosts': True,
    'users': [{'name': 'ansible', 'groups': ['sudo'], 'shell': '/bin/bash',
               'sudo': 'ALL=(ALL) NOPASSWD:ALL', 'lock_passwd': True,
               'ssh_authorized_keys': [(s / 'id_ed25519.pub').read_text().strip()]}],
    'ssh_pwauth': False,
    'ssh_keys': {'ed25519_private': (s / 'ssh_host_ed25519_key').read_text(),
                 'ed25519_public': (s / 'ssh_host_ed25519_key.pub').read_text()},
}
(s / 'user-data').write_text('#cloud-config\n' + yaml.safe_dump(config))
(s / 'meta-data').write_text('instance-id: notebook-ci\nlocal-hostname: notebook-ci\n')
inventory = {'all': {'children': {'hadoop_zookeeperserver': {'hosts': {
    'notebook-ci': {'ansible_host': '127.0.0.1', 'ansible_port': 2222,
        'ansible_user': 'ansible', 'ansible_python_interpreter': '/usr/bin/python3',
        'ansible_ssh_private_key_file': str(s / 'id_ed25519'),
        'ansible_ssh_common_args': '-o StrictHostKeyChecking=yes -o UserKnownHostsFile=' + str(s / 'known_hosts')}
}}}}}
(s / 'inventory.yml').write_text(yaml.safe_dump(inventory))
PY
    cloud-localds "$CI_STATE/seed.img" "$CI_STATE/user-data" "$CI_STATE/meta-data"
    qemu-img create -f qcow2 -F qcow2 -b "$CI_STATE/$image_name" "$CI_STATE/guest.qcow2" 10G
    qemu-system-x86_64 -enable-kvm -cpu host -smp 2 -m 3072 \
      -drive "file=$CI_STATE/guest.qcow2,format=qcow2,if=virtio" \
      -drive "file=$CI_STATE/seed.img,format=raw,if=virtio" \
      -netdev user,id=net0,hostfwd=tcp:127.0.0.1:2222-:22,hostfwd=tcp:127.0.0.1:22181-:2181 \
      -device virtio-net-pci,netdev=net0 -display none \
      -serial "file:$CI_ARTIFACTS/serial.log" -monitor none \
      -daemonize -pidfile "$CI_STATE/qemu.pid"
    ready=false
    for attempt in $(seq 1 90); do
      if ssh "${ssh_options[@]}" ansible@127.0.0.1 true 2>/dev/null; then ready=true; break; fi
      sleep 2
    done
    "$ready" || { echo 'Guest SSH did not become ready' >&2; exit 1; }
    ssh "${ssh_options[@]}" ansible@127.0.0.1 'sudo cloud-init status --wait'
    ssh "${ssh_options[@]}" ansible@127.0.0.1 'sudo apt-get update && sudo apt-get install -y python3 openjdk-11-jdk'
    ;;
  collect)
    if test -f "$CI_STATE/id_ed25519"; then
      timeout 30 ssh "${ssh_options[@]}" ansible@127.0.0.1 \
        'sudo journalctl -u zookeeper-server --no-pager; sudo systemctl status zookeeper-server --no-pager; sudo tail -n 100 /var/log/cloud-init-output.log' \
        > "$CI_ARTIFACTS/guest.log" 2>&1 || true
    fi
    ;;
  stop)
    if test -f "$CI_STATE/qemu.pid"; then
      kill "$(cat "$CI_STATE/qemu.pid")" || true
    fi
    ;;
  *) echo "Usage: $0 {start|collect|stop}" >&2; exit 2 ;;
esac
