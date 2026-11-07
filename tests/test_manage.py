import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import manage  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_config():
    manage.configure(dict(manage.DEFAULT_CONFIG))
    yield
    manage.configure(dict(manage.DEFAULT_CONFIG))


def _init_args(target, **overrides):
    values = {
        "dir": str(target),
        "repo": "https://github.com/example/demo-repo",
        "branch": "main",
        "site": "demo-repo",
        "skills_dir": None,
        "author": None,
        "force": False,
        "no_sync": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _new_args(name, **overrides):
    values = {
        "name": name,
        "category": "exam",
        "label": "測試 / 領域",
        "summary": "測試用技能",
        "triggers": "關鍵字A,關鍵字B",
        "package": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_load_config_defaults(tmp_path):
    config, has_config = manage.load_config(tmp_path)
    assert has_config is False
    assert config["repository"] == manage.DEFAULT_CONFIG["repository"]


def test_load_config_override(tmp_path):
    (tmp_path / manage.CONFIG_FILENAME).write_text(
        json.dumps({"repository": "https://github.com/acme/tools", "site_name": "tools"}),
        encoding="utf-8",
    )
    config, has_config = manage.load_config(tmp_path)
    assert has_config is True
    assert config["repository"] == "https://github.com/acme/tools"
    assert config["site_name"] == "tools"


def test_configure_derives_urls():
    manage.configure(
        {
            "repository": "https://github.com/acme/tools.git",
            "branch": "dev",
            "site_name": "Tools",
            "skills_dir": "~/.gemini/config/skills",
            "author": "acme",
        }
    )
    assert manage.REPO_URL == "https://github.com/acme/tools"
    assert manage.RAW_BASE_URL == "https://raw.githubusercontent.com/acme/tools/dev"
    assert manage.ARCHIVE_ROOT == "tools-dev"
    assert manage.SKILLS_DIR_PS == r"$HOME\.gemini\config\skills"
    assert manage.SKILLS_DIR_SH == "$HOME/.gemini/config/skills"
    assert "Tools" in manage.SHARED_CONTRACT_LINES[0]


def test_install_commands_use_config():
    manage.configure(
        {
            "repository": "https://github.com/acme/tools",
            "branch": "main",
            "site_name": "tools",
        }
    )
    ps = manage.ps_bundle_install(["demo"], "ok")
    sh = manage.sh_bundle_install(["demo"], "ok")
    single = manage.ps_single_install("demo", "ok")

    assert "github.com/acme/tools/archive/refs/heads/main.zip" in ps
    assert "tools-main" in ps
    assert "tools-main/demo/" in sh
    assert "mySkill" not in ps and "mySkill" not in sh
    assert "raw.githubusercontent.com/acme/tools/main/demo/SKILL.md" in single


def test_render_template_replaces_placeholders():
    template = (
        "站名 {{SITE_NAME}}｜{{REPO_URL}}｜{{RAW_BASE}}｜{{ARCHIVE_ROOT}}｜{{KIT_REPO}}"
    )
    rendered = manage._render_template(
        template,
        {
            "repository": "https://github.com/acme/mySkill-private",
            "branch": "main",
            "site_name": "mySkill-private",
        },
    )
    assert "站名 mySkill-private" in rendered
    assert "https://github.com/acme/mySkill-private" in rendered
    assert "https://raw.githubusercontent.com/acme/mySkill-private/main" in rendered
    assert "mySkill-private-main" in rendered
    assert manage.KIT_REPO in rendered
    assert "{{" not in rendered
    assert "mySkill-private-private" not in rendered


def test_render_template_rejects_unknown_placeholder():
    with pytest.raises(ValueError):
        manage._render_template("{{UNKNOWN_KEY}}", {"repository": "https://github.com/a/b"})


def test_contract_injection_idempotent_and_positioned():
    text = "# 角色\n\n內容。\n\n# 技能維護與自動更新指引\n\n步驟。\n"
    once = manage.inject_shared_contract(text)
    twice = manage.inject_shared_contract(once)

    assert once == twice
    assert manage.SHARED_CONTRACT_BLOCK in once
    assert once.index(manage.CONTRACT_BEGIN) < once.index("技能維護與自動更新指引")


def test_init_is_idempotent(tmp_path):
    target = tmp_path / "repo"
    assert manage.cmd_init(_init_args(target)) == 0
    first = {
        str(p.relative_to(target)): p.read_bytes()
        for p in target.rglob("*")
        if p.is_file()
    }

    assert manage.cmd_init(_init_args(target)) == 0
    second = {
        str(p.relative_to(target)): p.read_bytes()
        for p in target.rglob("*")
        if p.is_file()
    }
    assert first == second
    assert (target / manage.CONFIG_FILENAME).exists()
    assert (target / "GEMINI.md").exists()
    assert (target / "manage.cmd").exists()
    assert (target / ".github" / "workflows" / "skills-ci.yml").exists()
    assert not (target / "scripts" / "manage.py").exists()


def test_new_skill_passes_lint(tmp_path):
    target = tmp_path / "repo"
    assert manage.cmd_init(_init_args(target)) == 0

    manage.configure(manage.load_config(target)[0])
    assert manage.cmd_new(_new_args("demo-skill"), target) == 0
    assert manage.lint_skills(target) == 0
    assert manage.sync_all(target, check_only=True) == 0

    assert manage.cmd_new(_new_args("demo-pkg", package=True), target) == 0
    assert manage.lint_skills(target) == 0
    assert manage.sync_all(target, check_only=True) == 0
    assert (target / "demo-pkg" / "scripts").is_dir()


def test_new_rejects_duplicate_and_bad_input(tmp_path):
    target = tmp_path / "repo"
    assert manage.cmd_init(_init_args(target)) == 0
    manage.configure(manage.load_config(target)[0])

    assert manage.cmd_new(_new_args("demo-skill"), target) == 0
    assert manage.cmd_new(_new_args("demo-skill"), target) == 1
    assert manage.cmd_new(_new_args("Bad Name"), target) == 1
    assert manage.cmd_new(_new_args("no-triggers", triggers=""), target) == 1


def test_empty_repo_lint_and_sync(tmp_path):
    (tmp_path / manage.CONFIG_FILENAME).write_text(
        json.dumps({"repository": "https://github.com/acme/empty"}), encoding="utf-8"
    )
    manage.configure(manage.load_config(tmp_path)[0])

    assert manage.lint_skills(tmp_path) == 0
    assert manage.sync_all(tmp_path) == 0
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "還沒有任何技能" in readme
    assert manage.sync_all(tmp_path, check_only=True) == 0


def test_empty_dir_is_not_orphan(tmp_path):
    (tmp_path / "empty-dir").mkdir()
    assert manage.find_orphan_dirs(tmp_path) == []

    junk = tmp_path / "junk"
    junk.mkdir()
    (junk / "x.txt").write_text("x", encoding="utf-8")
    assert manage.find_orphan_dirs(tmp_path) == ["junk"]


def test_render_regenerates_generated_files(tmp_path):
    target = tmp_path / "repo"
    assert manage.cmd_init(_init_args(target)) == 0

    gemini = target / "GEMINI.md"
    gemini.write_text("被手改的內容\n", encoding="utf-8")
    assert manage.cmd_render(target) == 0
    assert "generated by skillkit" in gemini.read_text(encoding="utf-8")
    assert manage.cmd_render(target) == 0  # 第二次應為 no-op
