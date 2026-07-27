"""The dashboard runs the workflows itself, so the page and the things it
calls have to stay in agreement. These tests check the three seams where a
silent drift would leave the buttons dead:

  - the page must never contain a credential (the repo is public),
  - the workflow_dispatch inputs it sends must exist in the workflows,
  - the issue body it builds must parse back through apply_issue.

The last one runs the shipped JavaScript in node and feeds its output to
the real Python parser. Reimplementing the heading order in the test would
prove only that the test agrees with itself.
"""
import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

import apply_issue
import dashboard

ROOT = Path(__file__).parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
TEMPLATES = ROOT / ".github" / "ISSUE_TEMPLATE"

node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@pytest.fixture(scope="module")
def page():
    return dashboard.render(*dashboard.collect())


# --- the page is public ------------------------------------------------------

def test_page_contains_no_credential(page):
    """wine.html is world-readable. A token in it is a token published."""
    for marker in ("github_pat_", "ghp_", "gho_", "ghs_", "ghu_", "ghr_"):
        assert marker not in page.replace("github_pat_...", ""), f"looks like a token: {marker}"
    # The only place a token may appear is as a value read out of storage.
    assert 'localStorage.getItem(KEY)' in page
    assert "Bearer " + '" + token()' in page


def test_page_only_talks_to_the_github_api():
    """The shop table links out to shops, which is fine -- but the script
    itself must send the token to exactly one host."""
    hosts = set(re.findall(r'https://([a-z0-9.\-]+)', dashboard.JS))
    assert hosts == {"api.github.com"}, hosts


def test_page_loads_no_third_party_script(page):
    external = re.findall(r'<script[^>]*\ssrc=', page)
    assert not external, external


# --- dispatch inputs ---------------------------------------------------------

def dispatched_inputs(js):
    """{workflow file: {input names}} from the runWorkflow() calls in the JS."""
    calls = re.findall(r'runWorkflow\("([^"]+)",\s*\{(.*?)\}\s*,', js, re.S)
    return {
        wf: set(re.findall(r'^\s*([a-z_]+)\s*:', body, re.M))
        for wf, body in calls
    }


def test_every_dispatched_input_exists_in_its_workflow():
    sent = dispatched_inputs(dashboard.JS)
    assert sent, "no runWorkflow() calls found -- did the page stop dispatching?"
    for filename, names in sent.items():
        spec = yaml.safe_load((WORKFLOWS / filename).read_text())
        # `on` is parsed as the boolean True by YAML 1.1.
        trigger = spec.get("on", spec.get(True, {}))
        declared = set((trigger.get("workflow_dispatch") or {}).get("inputs", {}) or {})
        unknown = names - declared
        assert not unknown, f"{filename}: dispatching undeclared input(s) {unknown}"


def test_dispatched_workflows_accept_workflow_dispatch():
    for filename in dispatched_inputs(dashboard.JS):
        spec = yaml.safe_load((WORKFLOWS / filename).read_text())
        trigger = spec.get("on", spec.get(True, {}))
        assert "workflow_dispatch" in trigger, f"{filename} cannot be started manually"


# --- issue bodies ------------------------------------------------------------

RUNNER = """
global.window = { WINE: { repo: "owner/repo" } };
global.document = { addEventListener: function () {}, querySelectorAll: function () { return []; } };
global.localStorage = { getItem: function () { return null; }, setItem: function () {},
                        removeItem: function () {} };
global.fetch = function () { throw new Error("tests must not reach the network"); };
%(js)s
process.stdout.write(JSON.stringify({
  producer: window.WINE.builders.producerBody(%(producer)s),
  shop: window.WINE.builders.shopBody(%(shop)s)
}));
"""


def build_bodies(tmp_path, producer, shop):
    script = tmp_path / "run.js"
    script.write_text(RUNNER % {
        "js": dashboard.JS,
        "producer": json.dumps(producer),
        "shop": json.dumps(shop),
    })
    out = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@node
def test_producer_body_parses_into_the_right_fields(tmp_path):
    bodies = build_bodies(tmp_path, {
        "name": "Zzz Test Domaine", "aliases": "zzz test, zzztest", "region": "jura",
        "reference": "88", "verified": True, "bulk": "", "remove": False,
    }, {"name": "zzzshop", "url": "https://example.com", "remove": False})

    fields = apply_issue.parse_form(bodies["producer"])
    assert apply_issue.get(fields, "producer name", "name") == "Zzz Test Domaine"
    assert apply_issue.parse_aliases(apply_issue.get(fields, "alias")) == ["zzz test", "zzztest"]
    assert apply_issue.parse_region(apply_issue.get(fields, "region")) == "jura"
    # "Price quality" also contains "price", so heading order matters here.
    assert apply_issue.parse_price(apply_issue.get(fields, "reference", "price")) == 88
    assert apply_issue.checkbox(fields, "checked myself") is True
    assert apply_issue.checkbox(fields, "remove this producer") is False


@node
def test_producer_body_applies_end_to_end(tmp_path):
    bodies = build_bodies(tmp_path, {
        "name": "Zzz Test Domaine", "aliases": "zzz test", "region": "loire",
        "reference": "42", "verified": False, "bulk": "", "remove": False,
    }, {"name": "zzzshop", "url": "https://example.com", "remove": False})

    src = textwrap.dedent('''\
        PRODUCERS = {
            "Existing": ["existing"],
        }

        # ---------------------------------------------------------------------------
        # Shops to check.
        ''')
    book = {"producers": []}
    new_src, new_book, summary = apply_issue.handle_producer(
        apply_issue.parse_form(bodies["producer"]), src, book
    )
    assert '"Zzz Test Domaine": ["zzz test"]' in new_src
    entry = next(p for p in new_book["producers"] if p["name"] == "Zzz Test Domaine")
    assert entry["region"] == "loire"
    assert entry["reference_750_eur"] == 42
    assert entry["verified"] is False
    assert "Added producer" in summary


@node
def test_untouched_bulk_box_does_not_hijack_the_single_producer(tmp_path):
    """An empty bulk box must not be read as an entry -- that broke issue #20."""
    bodies = build_bodies(tmp_path, {
        "name": "Zzz Solo", "aliases": "zzz solo", "region": "rhone",
        "reference": "", "verified": False, "bulk": "", "remove": False,
    }, {"name": "zzzshop", "url": "https://example.com", "remove": False})
    fields = apply_issue.parse_form(bodies["producer"])
    assert apply_issue.parse_bulk_producers(
        apply_issue.get(fields, "bulk", "several at once", "one per line")
    ) == []


@node
def test_bulk_box_round_trips_through_the_code_fence(tmp_path):
    bodies = build_bodies(tmp_path, {
        "name": "", "aliases": "", "region": "", "reference": "", "verified": False,
        "bulk": "Zzz One | zzz one | jura | 30\nZzz Two | zzz two | burgundy | 60",
        "remove": False,
    }, {"name": "zzzshop", "url": "https://example.com", "remove": False})
    fields = apply_issue.parse_form(bodies["producer"])
    entries = apply_issue.parse_bulk_producers(
        apply_issue.get(fields, "bulk", "several at once", "one per line")
    )
    assert [e["name"] for e in entries] == ["Zzz One", "Zzz Two"]
    assert entries[1]["reference"] == 60


@node
def test_shop_body_applies_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(apply_issue, "write_shop_fixture", lambda name, url: None)
    bodies = build_bodies(tmp_path, {
        "name": "x", "aliases": "x", "region": "jura", "reference": "",
        "verified": False, "bulk": "", "remove": False,
    }, {"name": "zzzshop", "url": "https://shop.example.com", "remove": False})

    src = 'SHOPS = [\n]\n\n\nclass EmptyResponseError'
    new_src, summary = apply_issue.handle_shop(apply_issue.parse_form(bodies["shop"]), src)
    assert '"name": "zzzshop"' in new_src
    assert '"url": "https://shop.example.com"' in new_src
    assert '"verified": False' in new_src
    assert "Added shop" in summary


@node
def test_shop_removal_checkbox_is_recognised(tmp_path):
    bodies = build_bodies(tmp_path, {
        "name": "x", "aliases": "x", "region": "jura", "reference": "",
        "verified": False, "bulk": "", "remove": False,
    }, {"name": "zzzshop", "url": "", "remove": True})
    fields = apply_issue.parse_form(bodies["shop"])
    assert apply_issue.checkbox(fields, "remove this shop") is True


# --- the page's routing has to survive apply-config.yml's own gate -----------

def test_bodies_carry_the_heading_apply_config_routes_on():
    """apply-config.yml falls back to the form headings when the label is
    missing, so the JS must emit those exact strings."""
    workflow = (WORKFLOWS / "apply-config.yml").read_text()
    for heading in ("### Producer name", "### Short name"):
        assert heading in workflow
        assert heading.replace("### ", '"') + '"' in dashboard.JS or \
            heading.replace("### ", '("') in dashboard.JS, heading


def test_setup_check_watches_the_secrets_notify_actually_needs():
    """The page warns when the digest has nowhere to go. If notify.py starts
    requiring a different secret, the warning must follow it."""
    required = set(re.search(
        r'REQUIRED_SECRETS = \[(.*?)\]', dashboard.JS, re.S
    ).group(1).replace('"', "").replace("\n", "").split(","))
    required = {r.strip() for r in required if r.strip()}

    notify_src = (ROOT / "notify.py").read_text()
    checked = set(re.search(
        r'missing = \[k for k in \((.*?)\)', notify_src, re.S
    ).group(1).replace('"', "").split(","))
    checked = {c.strip() for c in checked if c.strip()}

    assert required == checked, f"page checks {required}, notify.py needs {checked}"


def test_setup_check_reads_names_not_values():
    """There is no API that returns a secret's value, and the page must not
    look like it wants one."""
    assert "/actions/secrets" in dashboard.JS
    assert "decrypt" not in dashboard.JS.lower()
    assert "encrypted_value" not in dashboard.JS


def test_region_dropdown_offers_exactly_the_accepted_regions():
    options = set(re.findall(r'<option value="([a-z]+)">', dashboard.region_options()))
    assert options == apply_issue.VALID_REGIONS
