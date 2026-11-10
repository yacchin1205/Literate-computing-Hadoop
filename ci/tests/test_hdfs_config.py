"""Check the single-NameNode branch without regressing HA name resolution."""
from pathlib import Path
import unittest
from xml.etree import ElementTree

from jinja2 import Environment, StrictUndefined
import yaml

ROOT = Path(__file__).resolve().parents[2]


class HDFSConfigTest(unittest.TestCase):
    def config(self, template, namenodes):
        variables = {}
        for path in (ROOT / 'playbooks/group_vars/all').iterdir():
            if path.is_file():
                variables.update(yaml.safe_load(path.read_text()) or {})
        variables.update(yaml.safe_load((ROOT / 'ci/hdfs-vars.yml').read_text()))
        variables.update(hadoop_namenode_servers=namenodes, dfs_nameservices='ci-test')
        source = (ROOT / 'playbooks/roles/base/templates' / template).read_text()
        rendered = Environment(undefined=StrictUndefined).from_string(source).render(variables)
        tree = ElementTree.fromstring(rendered)
        return {p.findtext('name'): p.findtext('value') for p in tree.findall('property')}

    def test_single_namenode_uses_direct_rpc_without_ha(self):
        core = self.config('core-site.xml.j2', ['127.0.0.1'])
        hdfs = self.config('hdfs-site.xml.j2', ['127.0.0.1'])
        self.assertEqual(core['fs.defaultFS'], 'hdfs://127.0.0.1:8020')
        self.assertEqual(hdfs['dfs.namenode.rpc-address'], '127.0.0.1:8020')
        self.assertFalse(any(key.startswith('dfs.ha.') for key in hdfs))
        self.assertFalse(any(key.startswith('dfs.client.failover.') for key in hdfs))

    def test_ha_preserves_logical_namespace_and_both_addresses(self):
        core = self.config('core-site.xml.j2', ['nn1', 'nn2'])
        hdfs = self.config('hdfs-site.xml.j2', ['nn1', 'nn2'])
        self.assertEqual(core['fs.defaultFS'], 'hdfs://ci-test')
        self.assertEqual(hdfs['dfs.ha.namenodes.ci-test'], 'nn1,nn2')
        self.assertEqual(hdfs['dfs.namenode.rpc-address.ci-test.nn1'], 'nn1:8020')
        self.assertEqual(hdfs['dfs.namenode.rpc-address.ci-test.nn2'], 'nn2:8020')
        self.assertEqual(hdfs['dfs.ha.automatic-failover.enabled'], 'true')
        self.assertIn('dfs.client.failover.proxy.provider.ci-test', hdfs)
        self.assertNotIn('dfs.namenode.rpc-address', hdfs)


if __name__ == '__main__':
    unittest.main()
