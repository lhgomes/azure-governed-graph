"""The console explains itself in everyday words: each page has guidance, each term is defined, the wording stays plain."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from governedgraph.admin import api

STATIC = Path(api.__file__).with_name("static")
# Words that belong in the technical documents, not in what a new user reads. `also` fields are exempt on purpose:
# they are where each plain term names its technical equivalent.
JARGON = ("snapshot", "overlay", "veto", "aggregate", "tier ", "strategy", "strategies", "ranking", "suspension",
          "governance", "assurance", "cohort", "telemetry", "uplift", "audit", "evidence pack", "synthetic")
MAX_WORDS_PER_SENTENCE = 28


@pytest.fixture(scope="module")
def guide():
    if not shutil.which("node"):
        pytest.skip("Node is needed to read the guidance file")
    script = (STATIC / "guide.js").read_text()
    run = subprocess.run(["node", "-e", f"global.window = {{}}; {script}; console.log(JSON.stringify(window.GUIDE))"],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def texts(node, path=""):
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from texts(item, f"{path}[{index}]")
    elif isinstance(node, dict):
        for key, item in node.items():
            if key not in {"also", "terms", "go"}:  # `terms` and `go` hold identifiers, not text a user reads
                yield from texts(item, f"{path}.{key}")


def menu_sections():
    page = (STATIC / "index.html").read_text()
    return re.findall(r'<button data-section="([a-z]+)"', page), page


def test_every_page_in_the_menu_has_guidance_and_defined_words(guide):
    sections, page = menu_sections()
    assert sections and set(sections) == set(guide["sections"])
    for name, item in guide["sections"].items():
        for key in ("title", "heading", "description", "what", "look", "can"):
            assert item[key].strip(), f"{name}.{key} is empty"
        assert item["terms"], f"{name} lists no words to explain"
        assert set(item["terms"]) <= set(guide["terms"]), f"{name} uses an undefined word"
        if name != "configuration":
            assert item["empty"].strip(), f"{name} has no message for an empty list"
            assert item["columns"], f"{name} has no columns"
        for label, help_text in item["columns"]:
            assert label.strip() and help_text.strip(), f"{name} has a column without help"
    for term in guide["terms"].values():
        assert term["name"].strip() and term["plain"].strip()
    for button in re.findall(r'<button data-section="[a-z]+"[^>]*>(.*?)</button>', page):
        assert "<small>" in button, f"menu item without a one-line hint: {button}"


def test_guidance_uses_everyday_words_and_short_sentences(guide):
    problems = []
    for path, text in texts(guide):
        lowered = text.lower()
        problems += [f"{path}: '{word.strip()}'" for word in JARGON if word in lowered]
        for sentence in re.split(r"(?<=[.?!])\s+", text):
            if len(sentence.split()) > MAX_WORDS_PER_SENTENCE:
                problems.append(f"{path}: a sentence of {len(sentence.split())} words")
    assert problems == []


def test_tasks_and_actions_point_at_things_that_exist(guide):
    for task in guide["tasks"]:
        assert task["go"] in set(guide["sections"]) | {"controls"}
        assert len(task["steps"]) >= 2
    assert set(guide["actions"]) == {"freeze", "revert_to_static", "suspend"}
    assert all(item["impact"].strip() for item in guide["actions"].values())
    assert len(guide["promises"]) >= 5


def test_the_page_loads_the_wording_before_the_script_and_keeps_the_required_label():
    page = (STATIC / "index.html").read_text()
    assert page.index("assets/guide.js") < page.index("assets/admin.js")
    assert "ILLUSTRATIVE - synthetic data" in page, "every view must stay labelled as synthetic"


def test_detail_views_lead_with_plain_words_and_keep_raw_records_behind_a_disclosure():
    script = (STATIC / "admin.js").read_text()
    assert "jsonDetails" not in script
    assert 'node("summary", "Technical details (for support)")' in script
    assert script.count("showDetails(") >= 8
