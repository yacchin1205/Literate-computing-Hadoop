"""Guard decisions made from notebook and ZooKeeper output."""
import ast
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scenario import ADDRESSES
from upgrade_inputs import enter_upgrade_inputs, inspect_upgrade_output, notebook_output


class UpgradeInputTest(unittest.TestCase):
    def test_finalize_waits_for_the_standby_to_consume_the_edit(self):
        session = SimpleNamespace(current_notebook='O12j_Hadoop - Verify and finalize upgrade.ipynb',
                                  records=[], upgrade={})
        active_only = '{"RollingUpgradeStatus": null}\n{"RollingUpgradeStatus": {"finalizeTime": 0}}\n'
        both = '{"RollingUpgradeStatus": null}\n{"RollingUpgradeStatus": null}\n'
        observed = lambda text: dict(outputs=[dict(output_type='stream', text=text)])
        reruns = []

        def run(index):
            reruns.append(index)
            return observed(both)

        controls = SimpleNamespace(run=run)
        with patch('upgrade_inputs.time.sleep'):
            inspect_upgrade_output(session, controls, 12, 'curl /jmx?qry=Hadoop:service=NameNode,name=NameNodeInfo',
                                   observed(active_only))
        self.assertEqual(reruns, [12])
        self.assertEqual([record['output'] for record in session.records], [active_only, both])

    def test_markdown_does_not_hide_stdout_or_stderr(self):
        book = dict(cells=[
            dict(cell_type='markdown', source='# Current roles'),
            dict(cell_type='code', outputs=[
                dict(output_type='stream', name='stdout', text=['nn1:8020 active\n']),
                dict(output_type='stream', name='stderr', text='diagnostic message\n'),
            ]),
        ])
        session = SimpleNamespace(page=SimpleNamespace(evaluate=lambda expression: book))
        self.assertEqual(notebook_output(session), 'nn1:8020 active\ndiagnostic message\n')

    def test_choose_followers_before_the_leader(self):
        session = SimpleNamespace(upgrade_zookeepers=set())
        selected = []
        controls = SimpleNamespace(edit=lambda index, source, reason: selected.append(
            ast.literal_eval(ast.parse(source).body[0].value)))

        def monitor(url, timeout):
            self.assertTrue(url.endswith('/commands/mntr'), url)
            address = url.split('/')[2].split(':')[0]
            state = 'leader' if address == ADDRESSES['nn1'] else 'follower'
            return io.BytesIO(json.dumps(dict(server_state=state, version='3.6.2')).encode())

        with patch('upgrade_inputs.urllib.request.urlopen', side_effect=monitor):
            for _ in range(3):
                enter_upgrade_inputs(session, controls, 0, "zookeeper_host = ''")
        self.assertEqual(set(selected), set(ADDRESSES.values()))
        self.assertEqual(selected[-1], ADDRESSES['nn1'])
        self.assertNotIn(ADDRESSES['nn1'], selected[:-1])


if __name__ == '__main__':
    unittest.main()
