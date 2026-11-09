from datetime import datetime
import shutil
import pytest
from log_viewer.reader import LogReader
from log_viewer.service import LogQueryService
from log_viewer.limits import Limits
from .case_loader import CATALOG, HERE

NOW=datetime.fromisoformat(CATALOG['default_clock'].replace('Z','+00:00'))

@pytest.fixture
def history(tmp_path):
    root=tmp_path/'logs'; shutil.copytree(HERE/'fixtures/history',root)
    return root

@pytest.fixture
def service(history):
    s=LogQueryService(LogReader(history),clock=lambda:NOW)
    yield s
    s.close()
