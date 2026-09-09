import os
from pathlib import Path
import pytest

def pytest_addoption(parser):
    parser.addoption('--goma-root',default=os.environ.get('GOMA_ROOT'),help='GOMA checkout for Gurobi integration tests')

@pytest.fixture
def solver_upstream(request):
    pytest.importorskip('gurobipy',reason='Gurobi is not installed in this environment')
    root=request.config.getoption('--goma-root')
    if not root:pytest.skip('Provide --goma-root or GOMA_ROOT for integration tests')
    from goma_batch.upstream import Upstream
    return Upstream(Path(root))
