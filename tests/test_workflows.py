"""The workflows are code too, and one of them broke on a space.

A shop probe was dispatched with `Lapangee, lavinoterie` in its "only" box.
probe.yml interpolated that straight into a shell command:

    [ -n "Lapangee, lavinoterie" ] && ARGS="$ARGS --only Lapangee, lavinoterie"
    python probe.py $ARGS
    probe.py: error: unrecognized arguments: lavinoterie

The space split one argument into two. On a public repo this shape is worse
than a bug -- anything a dispatcher types lands unquoted in a shell -- so the
rule is now enforced by a test rather than remembered: workflow inputs reach a
script through `env:`, quoted, never spliced into the command line.
"""
import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = sorted((Path(__file__).parent.parent / ".github" / "workflows").glob("*.yml"))

# ${{ inputs.x }}, ${{ github.event.issue.body }}, and step outputs derived
# from either -- all of them are text somebody outside this repo can choose.
UNTRUSTED_EXPRESSION = re.compile(r"\$\{\{\s*(inputs|github\.event|steps)\b")


def run_blocks(path):
    """Every `run:` script in a workflow, with its step name."""
    doc = yaml.safe_load(path.read_text())
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if "run" in step:
                yield step.get("name", "<unnamed>"), step["run"]


def test_workflows_exist():
    assert WORKFLOWS, "no workflows found -- this suite would pass vacuously"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_untrusted_expression_is_spliced_into_a_shell_command(path):
    """The rule that would have caught the probe bug before it shipped."""
    offenders = [
        (name, line.strip())
        for name, script in run_blocks(path)
        for line in script.splitlines()
        if UNTRUSTED_EXPRESSION.search(line)
    ]
    assert not offenders, (
        f"{path.name} splices a caller-controlled value into a shell command: "
        f"{offenders}. Pass it through env: and quote the variable instead."
    )


VARIABLE = re.compile(r"\$(?:\{)?([A-Z_][A-Z0-9_]*)")


def inside_double_quotes(line, index):
    """Whether `index` sits within a double-quoted span of `line`.

    Crude on purpose -- counting quotes is enough to tell `"$ONLY"` from
    `$ARGS`, and a shell parser here would be a second implementation of the
    thing being tested."""
    return line.count('"', 0, index) % 2 == 1


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_shell_variable_expansion_is_quoted(path):
    """`$ARGS` unquoted is what turned one argument into two."""
    offenders = []
    for name, script in run_blocks(path):
        for line in script.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # An assignment's own left-hand side, and $(...) command
            # substitution, are not the expansions this is about.
            for match in VARIABLE.finditer(stripped):
                if inside_double_quotes(stripped, match.start()):
                    continue
                if f'"${{{match.group(1)}[@]}}"' in stripped:
                    continue
                if f'{match.group(1)}=' in stripped[:match.start()]:
                    continue
                offenders.append((name, stripped))
                break
    assert not offenders, (
        f"{path.name} expands a variable unquoted: {offenders}. "
        "Word-splitting a value somebody typed is what broke the probe."
    )


# Variables the runner provides, or that a script may set for itself.
RUNNER_PROVIDED = {
    "GITHUB_ENV", "GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY", "GITHUB_PATH",
    "GITHUB_WORKSPACE", "GITHUB_TOKEN", "GITHUB_REF_NAME", "GITHUB_SHA",
    "HOME", "PATH", "RUNNER_TEMP", "RUNNER_OS", "CI",
}
ASSIGNED = re.compile(r"(?:^|\s|;)([A-Z_][A-Z0-9_]*)=|for\s+([A-Z_][A-Z0-9_]*)\s+in")


def step_env(job, step):
    """Names visible to a step: its own env plus the job's."""
    return set(step.get("env") or {}) | set(job.get("env") or {})


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_variable_a_script_reads_is_defined_for_that_step(path):
    """An env: block on the neighbouring step reads fine and expands to the
    empty string. `--kind ""` would have shipped exactly that way."""
    doc = yaml.safe_load(path.read_text())
    offenders = []
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            script = step.get("run")
            if not script:
                continue
            available = step_env(job, step) | RUNNER_PROVIDED
            for m in ASSIGNED.finditer(script):
                available.add(m.group(1) or m.group(2))
            for m in VARIABLE.finditer(script):
                if m.group(1) not in available:
                    offenders.append((step.get("name", "<unnamed>"), m.group(1)))
    assert not offenders, (
        f"{path.name} reads variables that step does not define: {sorted(set(offenders))}"
    )


def test_the_probe_passes_its_inputs_through_env():
    probe_yml = next(p for p in WORKFLOWS if p.name == "probe.yml")
    step = next(script for name, script in run_blocks(probe_yml) if "probe.py" in script)
    assert "--only" in step
    assert '"$ONLY"' in step or '"${ONLY}"' in step, \
        "the shop list must reach probe.py as one quoted argument"


def test_the_probe_saves_what_it_finds_by_default():
    """A read-only probe that discovers two working shops and commits nothing
    is the failure that left lavinoterie and pangee dark for three days. The
    common case -- "I added a shop, make it work" -- must be the default."""
    doc = yaml.safe_load(next(p for p in WORKFLOWS if p.name == "probe.yml").read_text())
    # YAML reads a bare `on:` key as the boolean True.
    triggers = doc.get("on") or doc[True]
    apply_input = triggers["workflow_dispatch"]["inputs"]["apply"]
    assert apply_input["default"] is True


def test_the_scraper_forces_a_report_only_for_a_dispatched_run():
    """The hourly schedule must stay quiet without news; a button press must
    not."""
    doc = yaml.safe_load(next(p for p in WORKFLOWS if p.name == "scraper.yml").read_text())
    env = doc["jobs"]["scrape"]["steps"][-2]["env"]
    assert "workflow_dispatch" in env["FORCE_REPORT"]
    assert "schedule" not in env["FORCE_REPORT"]
