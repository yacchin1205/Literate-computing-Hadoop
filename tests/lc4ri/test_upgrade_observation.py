"""The availability observation must cover every upgrade phase."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scenario import run_upgrade


class UpgradeObservationTest(unittest.TestCase):
    def test_observation_covers_platform_updates_and_final_checks(self):
        observer = SimpleNamespace(active=False, verified=False)
        executed = []

        def begin():
            observer.active = True

        def finish():
            observer.active = False

        def verify(session):
            self.assertFalse(observer.active)
            observer.verified = True

        def run_notebook(name, **kwargs):
            self.assertTrue(observer.active, 'Observation ended before ' + name)
            executed.append(name)

        observer.begin = begin
        observer.finish = finish
        observer.verify = verify
        session = SimpleNamespace(results=None, run_notebook=run_notebook)
        with patch('upgrade_observer.UpgradeObserver', return_value=observer):
            run_upgrade(session)
        self.assertTrue(any(name.startswith('O12k') for name in executed))
        self.assertTrue(observer.verified)
        self.assertFalse(observer.active)


if __name__ == '__main__':
    unittest.main()
