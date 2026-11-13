"""Read only AM diagnostic logs for the specified MapReduce applications."""
from pathlib import Path
import re
import sys

root = Path('/var/log/hadoop-yarn/userlogs')
for application_id in sys.argv[1:]:
    assert re.fullmatch(r'application_\d+_\d+', application_id), application_id
    containers = sorted((root / application_id).glob('container_*_000001'))
    if not containers:
        print(f'{application_id}: no local AM log directory', flush=True)
    for container in containers:
        for name in ('syslog', 'stderr', 'stdout', 'prelaunch.err'):
            path = container / name
            print(f'===== {path} =====', flush=True)
            if path.is_file():
                print(path.read_text(), flush=True)
            else:
                print('Log file absent', flush=True)
