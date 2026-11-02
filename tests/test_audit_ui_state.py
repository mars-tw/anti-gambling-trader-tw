"""Run the API-data lifecycle regression suite when Node is available."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_api_data_state_machine_regressions():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the browser-state regression suite")
    test_file = Path(__file__).with_name("ui_api_state.test.cjs")
    result = subprocess.run(
        [node, "--test", str(test_file)],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
