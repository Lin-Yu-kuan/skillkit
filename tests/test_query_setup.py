import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import manage


def repo_at(root, name="demo", usage=None, params=None, legacy=False):
    config = root / (manage.LEGACY_CONFIG_FILENAME if legacy else manage.CONFIG_FILENAME)
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text('{"repository": "https://github.com/example/demo"}', encoding="utf-8")
    folder = root / name
    folder.mkdir(parents=True)
    text = (
        f"---\nname: {name}\nversion: 1.0.0\ncategory: exam\nsummary: 測試技能\n"
        "description: 測試與版本更新\ntriggers:\n  - 題庫\n"
        + manage._contract_frontmatter(usage or [], params or [])
        + "---\n# 操作\n內文 --internal-flag 不可推導成正式參數\n"
    )
    (folder / "SKILL.md").write_text(text, encoding="utf-8")
    return folder


def query_args(name=None, **kwargs):
    return argparse.Namespace(name=name, repo=None, copy=False, example=1, **kwargs)


def test_catalog_only_reads_managed_repos_and_deduplicates(tmp_path):
    kit = tmp_path / "skillkit"
    kit.mkdir()
    repo_at(tmp_path / "owned", "one")
    repo_at(tmp_path / "legacy", "two", legacy=True)
    foreign = tmp_path / "installed" / "foreign"
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_text("---\nname: foreign\n---", encoding="utf-8")
    entries, roots, errors = manage.query_catalog(kit)
    assert {skill.name for _, skill in entries} == {"one", "two"}
    assert {root.name for root in roots} == {"owned", "legacy"}
    assert not errors


def test_explicit_repo_outside_siblings_and_unmanaged_rejection(tmp_path):
    kit = tmp_path / "tools" / "skillkit"
    kit.mkdir(parents=True)
    root = tmp_path / "其他位置" / "我的技能"
    repo_at(root)
    entries, roots, errors = manage.query_catalog(kit, str(root))
    assert len(entries) == 1 and roots == [root] and not errors
    entries, _, errors = manage.query_catalog(kit, str(tmp_path))
    assert not entries and errors


def test_stale_manifest_does_not_override_live_skill_or_write(tmp_path, capsys):
    folder = repo_at(tmp_path / "owned", usage=['/demo "<中文 路徑>"'])
    manifest = folder.parent / manage.MANIFEST_FILENAME
    manifest.write_text('{"skills": [{"name": "old-version"}]}', encoding="utf-8")
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert manage.cmd_query(query_args("demo"), tmp_path / "skillkit") == 0
    output = capsys.readouterr().out
    assert '/demo "<中文 路徑>"' in output
    assert "--internal-flag" not in output and "old-version" not in output
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


@pytest.mark.parametrize("help_flag", ["/?", "-h", "--help"])
def test_query_help_shows_skill_not_argparse(tmp_path, monkeypatch, capsys, help_flag):
    repo_at(tmp_path / "owned", usage=["/demo <path>"])
    monkeypatch.setattr(manage, "__file__", str(tmp_path / "skillkit" / "manage.py"))
    cwd = tmp_path / "unrelated" / "nested"
    (cwd / ".skillkit").mkdir(parents=True)
    (cwd / manage.CONFIG_FILENAME).write_text("broken", encoding="utf-8")
    monkeypatch.chdir(cwd)
    assert manage.main(["query", "demo", help_flag]) == 0
    assert "/demo <path>" in capsys.readouterr().out
    assert manage.main(["query", help_flag]) == 0


def test_exact_precedes_fuzzy_and_duplicate_copy_needs_repo(tmp_path, monkeypatch, capsys):
    repo_at(tmp_path / "repo-a", "demo", usage=["/demo a", "/demo -revise b"])
    repo_at(tmp_path / "repo-b", "demo", usage=["/demo other"])
    repo_at(tmp_path / "repo-c", "demo-extra")
    copied = []
    monkeypatch.setattr(manage, "copy_usage", copied.append)
    args = query_args("demo")
    assert manage.cmd_query(args, tmp_path / "skillkit") == 0
    output = capsys.readouterr().out
    assert "repo-a" in output and "repo-b" in output and "demo-extra" not in output
    args.copy = True
    assert manage.cmd_query(args, tmp_path / "skillkit") == 2
    assert not copied
    args.repo, args.example = "repo-a", 2
    assert manage.cmd_query(args, tmp_path / "skillkit") == 0
    assert copied == ["/demo -revise b"]


def test_fuzzy_multiple_candidates_and_missing_name(tmp_path, capsys):
    repo_at(tmp_path / "a", "demo-one")
    repo_at(tmp_path / "b", "demo-two")
    assert manage.cmd_query(query_args("demo"), tmp_path / "skillkit") == 2
    assert "候選" in capsys.readouterr().out
    assert manage.cmd_query(query_args("absent"), tmp_path / "skillkit") == 1
    assert manage.cmd_query(query_args("demo-one"), tmp_path / "skillkit") == 0
    assert "未宣告參數契約" in capsys.readouterr().out


def test_partial_scan_reports_errors_and_blocks_unqualified_copy(tmp_path, monkeypatch, capsys):
    repo_at(tmp_path / "good", usage=["/demo"])
    bad = tmp_path / "bad" / manage.CONFIG_FILENAME
    bad.parent.mkdir(parents=True)
    bad.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(manage, "copy_usage", lambda _: pytest.fail("must not copy"))
    args = query_args("demo")
    assert manage.cmd_query(args, tmp_path / "skillkit") == 1
    assert "/demo" in capsys.readouterr().out
    args.copy = True
    assert manage.cmd_query(args, tmp_path / "skillkit") == 2


@pytest.mark.parametrize("usage,params", [([], []), (["/demo"], ["bad contract"])])
def test_no_template_or_bad_contract_never_copies(tmp_path, monkeypatch, usage, params):
    repo_at(tmp_path / "owned", usage=usage, params=params)
    monkeypatch.setattr(manage, "copy_usage", lambda _: pytest.fail("must not copy"))
    args = query_args("demo")
    args.copy = True
    assert manage.cmd_query(args, tmp_path / "skillkit") == 1


def test_clipboard_data_is_not_shell_code(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(subprocess, "run", run)
    example = '/demo "中文 路徑" $(not-a-command) & literal'
    manage.copy_usage(example)
    command, options = calls[0]
    assert example not in command[-1]
    assert options["input"] == example.encode("utf-8")
    assert not options.get("shell")


@pytest.mark.parametrize("original", [r"C:\Windows", r"C:\Windows;", ""])
def test_path_add_is_idempotent_and_removable(original):
    target = r"D:\中文 路徑\skillkit"
    added = manage.update_path_value(original, target)
    assert manage.update_path_value(added, target) == added
    assert manage.update_path_value(added, target, remove=True).rstrip(";") == original.rstrip(";")


def test_path_preserves_unrelated_text_type_and_expanded_entries(monkeypatch):
    monkeypatch.setenv("SKILLKIT_TEST_PARENT", r"D:\tools")
    original = r'  C:\Windows ;"%SKILLKIT_TEST_PARENT%\SKILLKIT\";C:\other'
    assert manage.update_path_value(original, r"d:\tools\skillkit") == original
    assert manage.update_path_value(original, r"d:\tools\skillkit", remove=True) == r"  C:\Windows ;C:\other"
    with pytest.raises(ValueError):
        manage.update_path_value(original, "D:\\bad;path")


def test_setup_dry_run_idempotency_remove_and_registry_type(tmp_path, monkeypatch):
    kit = tmp_path / "skillkit"
    kit.mkdir()
    for name in ("skill.cmd", "skillkit.cmd"):
        (kit / name).write_text("@echo off", encoding="utf-8")
    monkeypatch.setattr(sys, "platform", "win32")
    state = {"value": r"C:\original", "kind": 1, "writes": 0}
    monkeypatch.setattr(manage, "read_user_path", lambda: (state["value"], state["kind"]))
    def write(value, kind):
        assert kind == 1
        state.update(value=value, writes=state["writes"] + 1)
    monkeypatch.setattr(manage, "write_user_path", write)
    monkeypatch.setattr(manage, "broadcast_environment_change", lambda: True)
    args = argparse.Namespace(dry_run=True, remove=False)
    assert manage.cmd_setup(args, kit) == 0 and state["writes"] == 0
    args.dry_run = False
    assert manage.cmd_setup(args, kit) == 0 and state["writes"] == 1
    assert manage.cmd_setup(args, kit) == 0 and state["writes"] == 1
    args.remove = True
    assert manage.cmd_setup(args, kit) == 0 and state["writes"] == 2
    assert state["value"] == r"C:\original"


def test_setup_missing_entrypoint_and_write_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    args = argparse.Namespace(dry_run=False, remove=False)
    assert manage.cmd_setup(args, tmp_path) == 1
    for name in ("skill.cmd", "skillkit.cmd"):
        (tmp_path / name).write_text("@echo off", encoding="utf-8")
    monkeypatch.setattr(manage, "read_user_path", lambda: ("", 2))
    def denied(*_):
        raise PermissionError("denied")
    monkeypatch.setattr(manage, "write_user_path", denied)
    assert manage.cmd_setup(args, tmp_path) == 1


def test_non_windows_setup_has_no_side_effects(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(manage, "read_user_path", lambda: pytest.fail("must not read registry"))
    assert manage.cmd_setup(argparse.Namespace(dry_run=False, remove=False), tmp_path) == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows command shims")
def test_windows_shims_from_unicode_space_path(tmp_path):
    import shutil
    kit = tmp_path / "中文 工具" / "skillkit"
    kit.mkdir(parents=True)
    source = Path(manage.__file__).resolve().parent
    for filename in ("manage.py", "skill.cmd", "skillkit.cmd"):
        shutil.copyfile(source / filename, kit / filename)
    repo = tmp_path / "中文 倉庫"
    repo_at(repo, usage=['/demo "<含 空白的路徑>"'])
    for filename, arguments in (("skill.cmd", ["demo", "/?", "--repo", str(repo)]),
                                ("skillkit.cmd", ["query", "demo", "-h", "--repo", str(repo)])):
        result = subprocess.run([str(kit / filename), *arguments], cwd=tmp_path,
                                capture_output=True, encoding="utf-8", timeout=15)
        assert result.returncode == 0, result.stderr
        assert '/demo "<含 空白的路徑>"' in result.stdout
    result = subprocess.run([str(kit / "skill.cmd"), "missing", "--repo", str(repo)],
                            cwd=tmp_path, capture_output=True, timeout=15)
    assert result.returncode == 1
