"""The Agent Skills are valid, self-consistent, evaluated, and their scripts and templates pass."""

from __future__ import annotations

import ast
import itertools
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from skills_ref import read_properties, validate

import feedback_manager

ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = ROOT / "feedback-manager-skills" / "skills"
SKILLS = sorted(path for path in SKILLS_ROOT.iterdir() if (path / "SKILL.md").is_file())
CORE = SKILLS_ROOT / "feedback-manager"
LEARNING = SKILLS_ROOT / "feedback-manager-learning"
EXTEND = SKILLS_ROOT / "feedback-manager-extend"
LINK = re.compile(r"\]\(([^)#<>\s]+)(?:#[^)]*)?\)")
# langsmith, a LangChain dependency, still calls this Python 3.14 deprecation.
STRICT_WARNINGS = [
    "-W",
    "error",
    "-W",
    "ignore:'asyncio.iscoroutinefunction' is deprecated:DeprecationWarning",
]
by_name = pytest.mark.parametrize("skill", SKILLS, ids=lambda path: path.name)


def run(*arguments: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - runs this repository's own files
        [sys.executable, *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=False,
        cwd=cwd,
    )


def pytest_run(target: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    # A private basetemp keeps the nested run away from the user-wide pytest temp directory.
    return run(
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        f"--basetemp={tmp_path}",
        f"--rootdir={target}",
        str(target),
        cwd=target,
    )


def test_the_three_skills_exist_and_skills_cli_discovers_them() -> None:
    assert [path.name for path in SKILLS] == [
        "feedback-manager",
        "feedback-manager-extend",
        "feedback-manager-learning",
    ]
    config = json.loads((ROOT / "skills.sh.json").read_text(encoding="utf-8"))
    assert [(ROOT / path).resolve() for path in config["paths"]] == [SKILLS_ROOT.resolve()]


@by_name
def test_the_skill_follows_the_agent_skills_specification(skill: Path) -> None:
    properties = read_properties(skill)

    assert validate(skill) == []
    assert properties.name == skill.name
    assert properties.license == "Apache-2.0"
    assert properties.metadata["version"] == feedback_manager.__version__
    assert properties.description.startswith("Use this skill when")
    assert "Do not use it" in properties.description
    assert len((skill / "SKILL.md").read_text(encoding="utf-8").splitlines()) < 500


@by_name
def test_references_are_one_level_deep_linked_and_every_link_resolves(skill: Path) -> None:
    body = (skill / "SKILL.md").read_text(encoding="utf-8")
    references = sorted((skill / "references").iterdir())
    assert all(path.is_file() and path.suffix == ".md" for path in references)
    for path in references:
        assert f"](references/{path.name})" in body, f"SKILL.md never links {path.name}"
    for page in skill.rglob("*.md"):
        for target in LINK.findall(page.read_text(encoding="utf-8")):
            if "://" in target:
                continue
            resolved = page.parent / target
            assert resolved.exists(), f"{page.name} links to {target}"
            # Exact case, so links that work on Windows and macOS also work on Linux.
            assert resolved.name in {entry.name for entry in resolved.parent.iterdir()}, target


@by_name
def test_every_bundled_script_and_asset_is_mentioned_by_the_skill(skill: Path) -> None:
    body = (skill / "SKILL.md").read_text(encoding="utf-8")
    bundled = [
        path
        for folder in ("scripts", "assets")
        if (skill / folder).is_dir()
        for path in (skill / folder).glob("*.py")
    ]
    assert bundled
    for path in bundled:
        assert f"{path.parent.name}/{path.name}" in body, f"SKILL.md never mentions {path.name}"


@by_name
def test_the_evals_are_well_formed(skill: Path) -> None:
    evals = json.loads((skill / "evals" / "evals.json").read_text(encoding="utf-8"))

    assert evals["skill_name"] == skill.name
    cases = evals["evals"]
    assert len(cases) >= 3
    assert len({case["id"] for case in cases}) == len(cases)
    for case in cases:
        assert case["prompt"].strip()
        assert case["expected_output"].strip()
        assert len(case["assertions"]) >= 3
        for file in case.get("files", []):
            assert (skill / "evals" / file).is_file(), file


@by_name
def test_the_trigger_queries_are_balanced_and_split(skill: Path) -> None:
    data = json.loads((skill / "evals" / "trigger_queries.json").read_text(encoding="utf-8"))
    queries = data["queries"]

    assert data["skill_name"] == skill.name
    assert len(queries) >= 16
    assert len({query["query"] for query in queries}) == len(queries)
    for split in ("train", "validation"):
        labels = [query["should_trigger"] for query in queries if query["split"] == split]
        assert True in labels, f"{split} has no query that should trigger"
        assert False in labels, f"{split} has no near-miss query"
    train = sum(query["split"] == "train" for query in queries)
    assert 0.5 <= train / len(queries) <= 0.7
    positives = sum(query["should_trigger"] for query in queries)
    assert 0.4 <= positives / len(queries) <= 0.6


def test_no_query_triggers_two_skills() -> None:
    def triggers(skill: Path) -> set[str]:
        data = json.loads((skill / "evals" / "trigger_queries.json").read_text(encoding="utf-8"))
        return {query["query"].lower() for query in data["queries"] if query["should_trigger"]}

    for first, second in itertools.combinations(SKILLS, 2):
        shared = triggers(first) & triggers(second)
        assert not shared, f"{first.name} and {second.name} both trigger on {shared}"


def test_the_eval_fixtures_run() -> None:
    agent = run(str(CORE / "evals" / "files" / "refund_agent" / "agent.py"))
    bot = run(str(LEARNING / "evals" / "files" / "support_bot" / "bot.py"))

    assert agent.returncode == 0, agent.stderr
    assert agent.stdout.strip() == "failed"
    assert bot.returncode == 0, bot.stderr


def test_the_setup_check_passes() -> None:
    completed = run(*STRICT_WARNINGS, str(CORE / "scripts" / "verify_setup.py"), "--langchain")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "FAIL" not in completed.stdout
    assert completed.stdout.rstrip().endswith("All checks passed.")


def test_the_setup_check_reports_json() -> None:
    completed = run(*STRICT_WARNINGS, str(CORE / "scripts" / "verify_setup.py"), "--json")
    report = json.loads(completed.stdout)

    assert completed.returncode == 0, completed.stderr
    assert report["ok"] is True
    assert report["summary"] == "All checks passed."
    assert {check["status"] for check in report["checks"]} <= {"PASS", "INFO"}


def test_the_setup_check_requires_the_package_dependency_ranges() -> None:
    source = (CORE / "scripts" / "verify_setup.py").read_text(encoding="utf-8")
    checked: dict[str, tuple[tuple[int, ...], tuple[int, ...]]] = {}
    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign):
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id in {"REQUIRED", "LANGCHAIN"}:
            checked |= ast.literal_eval(node.value)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    declared = {}
    for requirement in [*project["dependencies"], *project["optional-dependencies"]["langchain"]]:
        if "[" in requirement:  # agxai[langgraph] repeats the core agxai range
            continue
        match = re.fullmatch(r"([\w-]+)>=([\d.]+),<([\d.]+)", requirement)
        assert match, requirement
        name, low, high = match.groups()
        declared[name] = (
            tuple(int(part) for part in low.split(".")),
            tuple(int(part) for part in high.split(".")),
        )

    assert checked == declared


def test_the_learning_check_passes_and_reports_json() -> None:
    script = str(LEARNING / "scripts" / "verify_learning.py")
    text = run(*STRICT_WARNINGS, script)
    report = json.loads(run(*STRICT_WARNINGS, script, "--json").stdout)

    assert text.returncode == 0, text.stdout + text.stderr
    assert text.stdout.rstrip().endswith("All checks passed.")
    assert report["ok"] is True
    assert {check["status"] for check in report["checks"]} == {"PASS"}


@pytest.mark.parametrize(
    ("skill", "expected"),
    # The extend assets are 6 store-contract promises and 5 adapter checks.
    [(CORE, "6 passed"), (LEARNING, "6 passed"), (EXTEND, "11 passed")],
    ids=lambda value: value.name if isinstance(value, Path) else value,
)
def test_the_skill_test_templates_pass(skill: Path, expected: str, tmp_path: Path) -> None:
    completed = pytest_run(skill / "assets", tmp_path)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert expected in completed.stdout


def test_the_component_check_passes_a_conforming_store() -> None:
    completed = run(
        str(EXTEND / "scripts" / "check_component.py"),
        "store_template:TableFeedbackStore",
        "--path",
        str(EXTEND / "assets"),
        "--json",
    )
    report = json.loads(completed.stdout)

    assert completed.returncode == 0, completed.stderr
    assert report["ok"] is True
    assert report["contract"] == "FeedbackStoreContract"
    assert all(result["passed"] for result in report["results"])


def test_the_component_check_reports_a_broken_promise(tmp_path: Path) -> None:
    (tmp_path / "unordered.py").write_text(
        "from feedback_manager.storage import InMemoryFeedbackStore\n\n\n"
        "class Unordered(InMemoryFeedbackStore):\n"
        "    async def query(self, query):\n"
        "        return list(reversed(await super().query(query)))\n",
        encoding="utf-8",
    )
    completed = run(
        str(EXTEND / "scripts" / "check_component.py"),
        "unordered:Unordered",
        "--path",
        str(tmp_path),
    )

    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "FAIL  test_queries_filter_order_and_limit" in completed.stdout


def test_the_component_check_finds_every_flaw_in_the_eval_store() -> None:
    completed = run(
        str(EXTEND / "scripts" / "check_component.py"),
        "store:DictFeedbackStore",
        "--path",
        str(EXTEND / "evals" / "files" / "dict_store"),
    )

    assert completed.returncode == 1
    assert completed.stdout.rstrip().endswith("5 of 6 promises broken.")


def test_the_component_check_rejects_an_unloadable_factory() -> None:
    completed = run(str(EXTEND / "scripts" / "check_component.py"), "no_such_module:Store")

    assert completed.returncode == 2
    assert "cannot load no_such_module:Store" in completed.stdout + completed.stderr
