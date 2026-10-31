import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from mavc.corpus import run_cases


class CorpusTests(unittest.TestCase):
    def test_spooling_status_and_fresh_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)/'corpus'
            def command(case, run):
                return [sys.executable,'-c',
                        'import sys;from pathlib import Path;Path(sys.argv[1]).mkdir();'
                        'print("x"*200000);print("diagnosis",file=sys.stderr);sys.exit(int(sys.argv[2]))',
                        str(run),str(case['code'])]
            rows = run_cases([{'id':'ok','code':0},{'id':'failed','code':3}],folder,command,workers=2)
            self.assertEqual([r['status'] for r in rows],['completed','failed'])
            self.assertEqual(rows[1]['returncode'],3)
            self.assertGreater((folder/'ok/stdout.log').stat().st_size,200000)
            self.assertEqual((folder/'failed/stderr.log').read_text(),'diagnosis\n')
            self.assertNotEqual(rows[0]['run_dir'],rows[1]['run_dir'])
            self.assertEqual(len(json.loads((folder/'summary.json').read_text())['cases']),2)
            with self.assertRaises(FileExistsError):run_cases([{'id':'ok'}],folder,command)

    def assert_ended(self, pid):
        # An adopted zombie cannot run; init may reap it slightly later.
        path = Path(f'/proc/{pid}/stat')
        self.assertTrue(not path.exists() or path.read_text().split()[2]=='Z')

    def test_timeout_ends_spawned_child_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'corpus';pidfile=Path(tmp)/'child.pid'
            script='import subprocess,sys,time;from pathlib import Path;p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);Path(sys.argv[1]).write_text(str(p.pid));time.sleep(60)'
            result=run_cases([{'id':'slow'}],folder,lambda c,r:[sys.executable,'-c',script,str(pidfile)],timeout=.3)[0]
            self.assertEqual(result['status'],'timed_out')
            self.assert_ended(result['pid']);self.assert_ended(int(pidfile.read_text()))

    def test_interrupt_ends_owned_children_and_marks_queued_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);pidfile=root/'pid';folder=root/'corpus'
            driver='from mavc.corpus import run_cases;import sys;from pathlib import Path;run_cases([{"id":"active"},{"id":"queued"}],Path(sys.argv[1]),lambda c,r:[sys.executable,"-c","import time;from pathlib import Path;import os;Path('+repr(str(pidfile))+').write_text(str(os.getpid()));time.sleep(60)"])'
            process=subprocess.Popen([sys.executable,'-c',driver,str(folder)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                deadline=time.monotonic()+5
                while not pidfile.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(pidfile.exists())
                process.send_signal(signal.SIGTERM);process.wait(timeout=5)
                self.assert_ended(int(pidfile.read_text()))
                summary=json.loads((folder/'summary.json').read_text())
                self.assertTrue(summary['interrupted'])
                self.assertEqual([r['status'] for r in summary['cases']],['interrupted','interrupted'])
            finally:
                if process.poll() is None:process.kill();process.wait()

    def test_invalid_ids_are_reported_before_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'corpus'
            for cases in [[{'id':'../escape'}],[{'id':'a'},{'id':'a'}]]:
                with self.assertRaises(ValueError):run_cases(cases,folder,lambda c,r:[])
                self.assertFalse(folder.exists())

    def test_invalid_timeouts_cannot_create_unbounded_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'corpus'
            for timeout in [0,-1,float('nan'),float('inf'),-float('inf')]:
                with self.assertRaisesRegex(ValueError,'finite positive'):
                    run_cases([{'id':'a'}],folder,lambda c,r:[],timeout=timeout)
                self.assertFalse(folder.exists())
