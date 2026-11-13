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

for stdout in Path('/var/log/hadoop-yarn/userlogs').glob('application_*/container_*/stdout'):
    lines = stdout.read_text().splitlines()
    print(json.dumps({'container_stdout': str(stdout), 'logging_initialization': {
        'resource_found': any(line.startswith('log4j: Using URL [') for line in lines),
        'standard_resource_missing': 'log4j: Could not find resource: [container-log4j.properties].' in lines,
        'configuration_finished': 'log4j: Finished configuring.' in lines,
        'container_appender_configured': 'log4j: Parsed "CLA" options.' in lines,
    }}))
