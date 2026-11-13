"""Report only classpath and Hadoop installation paths from container launchers."""
import json
from pathlib import Path

FIELDS = {'CLASSPATH', 'HADOOP_CONF_DIR', 'HADOOP_COMMON_HOME', 'HADOOP_HDFS_HOME',
          'HADOOP_YARN_HOME', 'HADOOP_MAPRED_HOME'}

for launcher in Path('/hadoop/tmp').rglob('launch_container.sh'):
    fields = {}
    text = launcher.read_text()
    for line in text.splitlines():
        if line.startswith('export '):
            name, separator, value = line[7:].partition('=')
            if name in FIELDS:
                fields[name] = value
    print(json.dumps({'launcher': str(launcher), 'environment': fields,
                      'default_logging_config': '-Dlog4j.configuration=container-log4j.properties' in text}))
