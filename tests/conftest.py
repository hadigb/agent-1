import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "examples" / "sample-esb"


@pytest.fixture
def sample_project(tmp_path):
    """A private copy of the sample project (so tests can edit files and use a fresh DB)."""
    dst = tmp_path / "sample-esb"
    shutil.copytree(SAMPLE / "src", dst / "src")
    shutil.copy(SAMPLE / "apidocgen.yaml", dst / "apidocgen.yaml")
    from apidocgen.config import Config
    from apidocgen.project import Project

    cfg = Config.load(str(dst / "apidocgen.yaml"))
    p = Project(cfg)
    p.scan()
    yield p
    p.close()
