import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def world(tmp_path_factory):
    """Синтетический мир как его видит агент утром: витрины отстают на 3 дня."""
    from synth.generate import generate
    p = tmp_path_factory.mktemp("world") / "world.db"
    generate(p, users=12000, seed=42)
    return p


@pytest.fixture(scope="session")
def truth(world):
    return json.loads(world.with_name("scenarios.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def fresh_world(world, tmp_path_factory):
    """Тот же мир после того, как пайплайн build_metrics пересобрал витрины."""
    from synth.build_metrics import build
    p = tmp_path_factory.mktemp("fresh") / "world.db"
    shutil.copy(world, p)
    build(p)
    return p


def _engine(path):
    from pa_agent.db import get_engine
    return get_engine(f"sqlite:///file:{path}?mode=ro&uri=true")


@pytest.fixture()
def engine(world):
    return _engine(world)


@pytest.fixture()
def fresh_engine(fresh_world):
    return _engine(fresh_world)


@pytest.fixture()
def policy(tmp_path, monkeypatch, world):
    from pa_agent.policy import Policy
    monkeypatch.setenv("PA_STATE", str(tmp_path / "state.db"))
    monkeypatch.setenv("PA_AUDIT", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("PA_SQLITE_PATH", str(world))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    pol = Policy.load()
    pol.raw["workspace"] = str(tmp_path / "ws")
    return pol
