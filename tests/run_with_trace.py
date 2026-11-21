"""Run a test child with an on-demand traceback for startup timeouts."""
import faulthandler
from pathlib import Path
import runpy
import signal
import sys

faulthandler.register(signal.SIGUSR1, all_threads=True)
sys.argv = sys.argv[1:]
sys.path.insert(0, str(Path(sys.argv[0]).resolve().parent))
runpy.run_path(sys.argv[0], run_name='__main__')
