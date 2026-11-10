"""Execute a CI notebook, preserving partial output even on failure."""
import os
from pathlib import Path
import sys

import nbformat
from nbclient import NotebookClient

root = Path(__file__).resolve().parents[1]
source = root / 'ci/notebooks/zookeeper.ipynb'
output = Path(os.environ['CI_ARTIFACTS']) / source.name
notebook = nbformat.read(source, as_version=4)
client = NotebookClient(
    notebook, timeout=1200, kernel_name='python3',
    resources={'metadata': {'path': str(root)}},
)
try:
    client.execute()
finally:
    nbformat.write(notebook, output)
    print(f'Notebook evidence: {output}', flush=True)
