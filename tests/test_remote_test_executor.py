from types import SimpleNamespace

import pytest
from deploy import run_remote_tests as executor


@pytest.mark.parametrize("count", [23, 1410])
def test_all_shards_collect_once_cover_every_node_and_keep_60_second_test_timeout(
    monkeypatch, count
):
    nodes = [f"tests/test_example.py::test_case[{number}]" for number in range(count)]
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout="\n".join([*nodes, "23 tests collected"]))

    monkeypatch.setattr(executor.subprocess, "run", run)
    environment = {"TEST_DATABASE_INSTANCE_ID": "isolated"}
    executor._run_test_shards(environment)
    expected = []
    for index in range(8):
        shard = nodes[index::8]
        expected.extend(shard[offset : offset + 88] for offset in range(0, len(shard), 88))
    assert len(calls) == len(expected) + 1
    assert calls[0][0][-1] == "--collect-only"
    assert all(kwargs["timeout"] == 60 and kwargs["env"] is environment for _, kwargs in calls)
    actual = []
    for index, (command, _) in enumerate(calls[1:]):
        assert command[1:4] == ["-m", "pytest", "-q"]
        assert command[4:] == expected[index]
        assert len(command[4:]) <= 88
        actual.extend(command[4:])
    assert sorted(actual) == sorted(nodes)


def test_collection_failure_does_not_run_any_shard(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        raise RuntimeError("collection failed")

    monkeypatch.setattr(executor.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="collection failed"):
        executor._run_test_shards({})
    assert len(calls) == 1


def test_targeted_tests_upgrade_and_check_same_database_before_pytest(monkeypatch):
    calls = []
    environment = {"TEST_DATABASE_INSTANCE_ID": "isolated"}
    monkeypatch.setattr(executor, "_environment", lambda: environment)
    monkeypatch.setattr(executor.sys, "argv", ["runner", "pytest", "tests/test_example.py"])
    monkeypatch.setattr(executor, "_run", lambda command, env: calls.append((command, env)))
    executor.main()
    assert [command for command, _ in calls[:2]] == [
        ["alembic", "upgrade", "head"],
        ["alembic", "check"],
    ]
    assert all(env is environment for _, env in calls)
    assert calls[-1][0][-1] == "tests/test_example.py"
