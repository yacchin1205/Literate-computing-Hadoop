"""Find the Pi application's IDs in the executed deployment notebook."""
import json
from pathlib import Path
import re
import sys

from notebook_controls import source

notebook = Path(sys.argv[1])
if notebook.is_file():
    ids = set()
    for cell in json.loads(notebook.read_text())['cells']:
        if cell['cell_type'] == 'code' and 'mapreduce-examples-*.jar pi' in source(cell):
            for output in cell['outputs']:
                if output['output_type'] == 'stream':
                    ids.update(re.findall(r'application_\d+_\d+', ''.join(output['text'])))
    print(' '.join(sorted(ids)))
else:
    print('D12 working copy unavailable; no MapReduce application IDs to collect', file=sys.stderr)
