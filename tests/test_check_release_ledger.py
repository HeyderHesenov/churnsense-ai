"""The best-effort consistency check on the release ledger: its rules, in real Git repositories.

Every ledger here is invented. These tests create throwaway repositories under ``tmp_path``
and run the script as CI does; nothing reads a real ledger, a report or a dataset.
"""

from __future__ import annotations

import ast
import json
import os
import random
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from scripts import check_release_ledger as check

from churnsense.evaluation import disclosure, release
from churnsense.exceptions import ReleaseRefused
from tests import ledger_payloads as payloads

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_release_ledger.py"
WORKFLOW = ROOT / ".github" / "workflows" / "held-out-release-governance.yml"
NOW = "2026-11-07T00:00:00+00:00"
IDENTITY = {**dict.fromkeys(release._HASH_KEYS, "a" * 64), "threshold": 0.36}
OTHER = {**IDENTITY, "threshold": 0.37}
FIGURES = {
    "threshold": 0.36,
    "n": 1000,
    "positives": 300,
    "tn": 500,
    "fp": 200,
    "fn": 150,
    "tp": 150,
}


def _ledger(state: str, identity: dict | None, released: dict | None = None) -> str:
    """Written by the project's own encoder, so the script is tested against the real format."""
    at = None if identity is None else NOW
    return release._encode(release.Ledger(state, identity, at, released))


def _released() -> dict:
    return release.disclosure.release(FIGURES)


OPEN_TEXT = release.open_ledger_text()
RESERVED_TEXT = _ledger(release.RESERVED, IDENTITY)
PUBLISHED_TEXT = _ledger(release.PUBLISHED, IDENTITY, _released())
WITHHELD_TEXT = _ledger(release.PUBLISHED, IDENTITY)
SECOND_TEXT = _ledger(release.PUBLISHED, OTHER, {**_released(), "threshold": 0.37})


def _record(target_text: str = RESERVED_TEXT, **changes) -> dict:
    target = json.loads(target_text)
    return {
        "previous_ledger_digest": target["digest"],
        "previous_identity": target["identity"],
        "reason": "the scoring code needed a fix",
        "reviewed_in": "https://github.example/pull/1",
        "no_figures_published": True,
        "recorded_at": NOW,
        **changes,
    }


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


# --- the two copies of the format agree ---------------------------------------------------


def test_the_script_knows_the_fields_the_project_writes():
    assert check.IDENTITY_KEYS == release._IDENTITY_KEYS
    assert check._LEDGER_KEYS == release._LEDGER_KEYS
    assert (check.OPEN, check.RESERVED, check.PUBLISHED) == (
        release.OPEN,
        release.RESERVED,
        release.PUBLISHED,
    )
    assert check.LEDGER == "governance/held_out_release.json"
    assert release.LEDGER_FILE.name == Path(check.LEDGER).name


def test_the_script_holds_the_project_s_rule_and_limits():
    assert check.FORMAT == release._FORMAT
    assert check.POLICY == disclosure.POLICY
    assert check.MIN_CELL == disclosure.MIN_CELL
    assert check.DIGITS == disclosure.DIGITS
    assert check.MAX_COUNT == disclosure._MAX_COUNT
    assert check.RELEASED_KEYS == disclosure.RELEASED_KEYS
    assert check.MAX_BYTES == release._MAX_LEDGER_BYTES


@pytest.mark.parametrize("text", [OPEN_TEXT, RESERVED_TEXT, PUBLISHED_TEXT, WITHHELD_TEXT])
def test_the_script_reads_what_the_project_writes(text):
    assert check.decode_ledger(text, "here")["digest"] == json.loads(text)["digest"]


# --- the schema, in both readers ----------------------------------------------------------


@pytest.mark.parametrize("text", payloads.VALID.values(), ids=payloads.VALID.keys())
def test_both_readers_accept_what_the_schema_allows(text):
    assert check.decode_ledger(text, "here")["state"] == release._decode(text).state


@pytest.mark.parametrize(
    "name", [n for n in payloads.VALID if json.loads(payloads.VALID[n])["state"] != "open"]
)
def test_the_check_lets_a_valid_ledger_land_on_an_open_one(name):
    assert check.check_transition(OPEN_TEXT, payloads.VALID[name])


@pytest.mark.parametrize("text", payloads.INVALID.values(), ids=payloads.INVALID.keys())
def test_both_readers_refuse_what_the_schema_does_not_allow(text):
    with pytest.raises(check.Refused):
        check.decode_ledger(text, "here")
    with pytest.raises(ReleaseRefused):
        release._decode(text)


@pytest.mark.parametrize("text", payloads.INVALID.values(), ids=payloads.INVALID.keys())
@pytest.mark.parametrize("target", [OPEN_TEXT, RESERVED_TEXT], ids=["from-open", "from-reserved"])
def test_a_rejected_publication_cannot_land(target, text):
    with pytest.raises(check.Refused):
        check.check_transition(target, text)


@pytest.mark.parametrize("value", [-1, True, False, 1.0, "1", None, check.MAX_COUNT + 1], ids=repr)
def test_a_count_is_a_whole_number_in_range_on_its_own_account(value):
    """Not left to the floor or the totals, which happen to catch these today."""
    assert not check._is_count(value)


@pytest.mark.parametrize("value", [0, 1, 20, check.MAX_COUNT])
def test_a_count_in_range_is_one(value):
    assert check._is_count(value)


def test_a_chosen_value_in_a_fingerprint_passes_both_readers():
    """A valid shape is not a true value: the limit ``SECURITY.md`` states, kept in sight."""
    text = payloads.seal(
        payloads.published_body(identity={**payloads.IDENTITY, "dataset_sha256": "8123" + "0" * 60})
    )
    assert check.decode_ledger(text, "here")
    assert release._decode(text)


def _one_table(rng: random.Random) -> str:
    """A table near the floor, sometimes with one field made into something it should not be."""
    sizes = [0, 1, 18, 19, 20, 20, 21, 21, 21, 40, 40, 500, 500, 10**6, 10**6, 10**8]
    tn, fp, fn, tp = (rng.choice(sizes) for _ in range(4))
    released = (
        payloads.table(tn, fp, fn, tp)
        if tp + fp and tp + fn and tn + fp + fn + tp
        else {"threshold": 0.36}
    )
    if rng.random() < 0.5:
        key = rng.choice([*released, "brier"])
        released = {
            **released,
            key: rng.choice([None, True, False, 0, 1, 1.0, 20, 20.0, "20", -1, 0.5, [], {}]),
        }
    return payloads.seal(payloads.published_body(released=released))


def _accepts(decode, refusal: type[Exception], text: str) -> bool:
    try:
        decode(text)
    except refusal:
        return False
    return True


def test_the_two_readers_agree_on_tables_nobody_chose():
    """Two copies of a rule drift. Here they meet on ground neither was written for."""
    rng = random.Random(20261107)
    outcomes = []
    for _ in range(600):
        text = _one_table(rng)
        by_project = _accepts(release._decode, ReleaseRefused, text)
        by_script = _accepts(lambda t: check.decode_ledger(t, "here"), check.Refused, text)
        assert by_project == by_script, text
        outcomes.append(by_project)
    assert sum(outcomes) >= 20, "the comparison must include tables that pass"
    assert len(outcomes) - sum(outcomes) >= 20, "and tables that do not"


def test_the_script_imports_nothing_from_the_project_or_outside_the_standard_library():
    tree = ast.parse(SCRIPT.read_text())
    names = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert names <= set(sys.stdlib_module_names) | {"__future__"}


# --- the rules ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "after"),
    [
        pytest.param(None, OPEN_TEXT, id="the-ledger-arrives"),
        pytest.param(OPEN_TEXT, OPEN_TEXT, id="open-unchanged"),
        pytest.param(OPEN_TEXT, RESERVED_TEXT, id="open-to-reserved"),
        pytest.param(OPEN_TEXT, PUBLISHED_TEXT, id="open-to-published"),
        pytest.param(OPEN_TEXT, WITHHELD_TEXT, id="open-to-withheld"),
        pytest.param(RESERVED_TEXT, RESERVED_TEXT, id="reserved-unchanged"),
        pytest.param(RESERVED_TEXT, PUBLISHED_TEXT, id="reserved-to-published"),
        pytest.param(RESERVED_TEXT, WITHHELD_TEXT, id="reserved-to-withheld"),
        pytest.param(PUBLISHED_TEXT, PUBLISHED_TEXT, id="published-unchanged"),
    ],
)
def test_what_is_allowed(before, after):
    assert check.check_transition(before, after)


@pytest.mark.parametrize(
    ("before", "after", "reason"),
    [
        pytest.param(OPEN_TEXT, None, "deleted", id="open-deleted"),
        pytest.param(RESERVED_TEXT, None, "deleted", id="reserved-deleted"),
        pytest.param(PUBLISHED_TEXT, None, "deleted", id="published-deleted"),
        pytest.param(None, None, "deleted", id="never-there"),
        pytest.param(None, RESERVED_TEXT, "arrive", id="arrives-reserved"),
        pytest.param(None, PUBLISHED_TEXT, "arrive", id="arrives-published"),
        pytest.param(PUBLISHED_TEXT, OPEN_TEXT, "immutable", id="published-back-to-open"),
        pytest.param(PUBLISHED_TEXT, RESERVED_TEXT, "immutable", id="published-back-to-reserved"),
        pytest.param(PUBLISHED_TEXT, SECOND_TEXT, "immutable", id="a-second-release"),
        pytest.param(PUBLISHED_TEXT, WITHHELD_TEXT, "immutable", id="figures-taken-away"),
        pytest.param(WITHHELD_TEXT, PUBLISHED_TEXT, "immutable", id="figures-put-back"),
        pytest.param(WITHHELD_TEXT, OPEN_TEXT, "immutable", id="withheld-is-final"),
        pytest.param(RESERVED_TEXT, OPEN_TEXT, "never goes back", id="reserved-back-to-open"),
        pytest.param(RESERVED_TEXT, SECOND_TEXT, "own identity", id="reserved-other-identity"),
        pytest.param(
            RESERVED_TEXT, _ledger(release.RESERVED, OTHER), "not rewritten", id="re-reserved"
        ),
        pytest.param(OPEN_TEXT, "not json", "not valid", id="damaged-proposal"),
        pytest.param(OPEN_TEXT, "{}", "wrong fields", id="empty-proposal"),
        pytest.param("not json", OPEN_TEXT, "not valid", id="damaged-target"),
        pytest.param(
            OPEN_TEXT,
            RESERVED_TEXT.replace('"threshold": 0.36', '"threshold": 0.5'),
            "digest",
            id="edited-by-hand",
        ),
    ],
)
def test_what_is_refused(before, after, reason):
    with pytest.raises(check.Refused, match=reason):
        check.check_transition(before, after)


def test_a_reservation_keeps_its_date():
    later = release._encode(
        release.Ledger(release.PUBLISHED, IDENTITY, "2030-01-01T00:00:00+00:00", _released())
    )
    with pytest.raises(check.Refused, match="date"):
        check.check_transition(RESERVED_TEXT, later)


def test_an_identity_with_another_shape_is_refused():
    body = json.loads(RESERVED_TEXT)
    del body["identity"]["runtime_sha256"]
    with pytest.raises(check.Refused, match="hold together"):
        check.check_transition(OPEN_TEXT, payloads.seal(body))


_NEXT_POLICY = check.POLICY + 1
_NEXT_OPEN = payloads.seal(payloads.open_body(policy=_NEXT_POLICY))


def test_a_new_policy_may_change_only_an_open_ledger():
    assert check.check_transition(
        OPEN_TEXT, _NEXT_OPEN, declared_policies=(_NEXT_POLICY, _NEXT_POLICY)
    )
    for body in (payloads.reserved_body, payloads.published_body):
        with pytest.raises(check.Refused, match="policy"):
            check.check_transition(
                OPEN_TEXT,
                payloads.seal(body(policy=_NEXT_POLICY)),
                declared_policies=(_NEXT_POLICY, _NEXT_POLICY),
            )


@pytest.mark.parametrize(
    "policy",
    [check.POLICY - 1, check.POLICY + 2, 99, 10**30, -1, 0, True, False, 3.0, "3", None, [3]],
    ids=repr,
)
def test_an_open_ledger_stands_only_under_this_policy_or_the_next(policy):
    text = payloads.seal(payloads.open_body(policy=policy))
    with pytest.raises(check.Refused, match="policy"):
        check.decode_ledger(text, "here")
    with pytest.raises(ReleaseRefused):
        release._decode(text)


def test_a_bool_is_not_a_policy_even_where_it_would_equal_one(monkeypatch):
    """Not left to today's value of ``POLICY``: under 1, ``True`` would otherwise compare equal."""
    monkeypatch.setattr(check, "POLICY", 1)
    assert check._policy_problem(check.OPEN, 1) is None
    assert check._policy_problem(check.OPEN, True) == "has a policy that is not a version"


def test_the_readers_differ_on_exactly_one_policy_and_on_purpose():
    """The script runs from the target, so a coordinated bump has to pass under the old value."""
    assert check.decode_ledger(OPEN_TEXT, "here")
    assert release._decode(OPEN_TEXT)
    assert check.decode_ledger(_NEXT_OPEN, "here")
    with pytest.raises(ReleaseRefused, match="another policy"):
        release._decode(_NEXT_OPEN)


@pytest.mark.parametrize(
    "declared",
    [
        pytest.param((), id="nothing-declared"),
        pytest.param((check.POLICY, check.POLICY), id="the-code-did-not-move"),
        pytest.param((_NEXT_POLICY, check.POLICY), id="only-the-script-moved"),
        pytest.param((check.POLICY, _NEXT_POLICY), id="only-the-rule-moved"),
        pytest.param((None, _NEXT_POLICY), id="a-file-without-a-constant"),
        pytest.param((_NEXT_POLICY + 1, _NEXT_POLICY + 1), id="the-code-went-further"),
    ],
)
@pytest.mark.parametrize("target", [OPEN_TEXT, None], ids=["over-open", "arriving"])
def test_the_next_policy_needs_its_code_in_the_same_change(target, declared):
    with pytest.raises(check.Refused, match="same change"):
        check.check_transition(target, _NEXT_OPEN, declared_policies=declared)


def _bump(repo: Repo, script: int | None, rule: int | None) -> str:
    """A change setting the ledger to the next policy, and each constant as given."""
    files = {check.SCRIPT_PATH: script, check.RULE_PATH: rule}
    for path, value in files.items():
        if value is not None:
            target = repo.path / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f'"""A stand-in."""\n\nPOLICY = {value}\n')
    return repo.commit(_NEXT_OPEN, "the next policy")


def test_a_coordinated_policy_bump_passes_on_real_history(repo):
    head = _bump(repo, _NEXT_POLICY, _NEXT_POLICY)
    done = repo.run("HEAD~1", head)
    assert done.returncode == 0, done.stderr
    assert "newer policy" in done.stdout


@pytest.mark.parametrize(
    ("script", "rule"),
    [(None, None), (_NEXT_POLICY, None), (None, _NEXT_POLICY), (check.POLICY, _NEXT_POLICY)],
    ids=["ledger-alone", "script-alone", "rule-alone", "script-behind"],
)
def test_an_uncoordinated_policy_bump_fails_on_real_history(repo, script, rule):
    head = _bump(repo, script, rule)
    done = repo.run("HEAD~1", head)
    assert done.returncode == 1
    assert "same change" in done.stderr


def test_the_constant_the_check_looks_for_is_the_one_in_both_files():
    for path, name in ((SCRIPT, "script"), (ROOT / check.RULE_PATH, "rule")):
        assert check.POLICY_LINE.findall(path.read_text()) == [str(check.POLICY)], name


def test_an_open_ledger_never_moves_to_an_older_policy():
    older = payloads.seal(payloads.open_body(policy=check.POLICY - 1))
    with pytest.raises(check.Refused, match="policy"):
        check.check_transition(OPEN_TEXT, older)
    newer = payloads.seal(payloads.open_body(policy=check.POLICY + 1))
    with pytest.raises(check.Refused, match="older policy"):
        check.check_transition(newer, OPEN_TEXT)


@pytest.mark.parametrize(
    "after",
    [PUBLISHED_TEXT, WITHHELD_TEXT, RESERVED_TEXT],
    ids=["published", "withheld", "reserved"],
)
def test_a_release_cannot_land_under_a_policy_older_than_the_open_ledger_s(after):
    """A half-finished policy bump: the target already moved on, this script has not."""
    newer = payloads.seal(payloads.open_body(policy=check.POLICY + 1))
    with pytest.raises(check.Refused, match="older policy"):
        check.check_transition(newer, after)


def test_a_file_git_cannot_read_is_not_a_file_that_is_absent(repo):
    """Read as absent, a damaged target would let a ledger "arrive" and a record pass unseen."""
    blob = repo.git("rev-parse", f"HEAD:{check.LEDGER}").stdout.strip()
    (repo.path / ".git" / "objects" / blob[:2] / blob[2:]).unlink()
    done = repo.run("HEAD", "HEAD")
    assert done.returncode == 1
    assert f"refused: {check.LEDGER} cannot be read" in done.stderr
    assert "Traceback" not in done.stderr


def test_a_ledger_that_is_not_text_is_refused_by_name_not_by_a_traceback(repo):
    repo.git("checkout", "-q", "-b", "binary")
    (repo.path / check.LEDGER).write_bytes(b"\xff\xfe not text")
    repo.git("add", "-A")
    repo.git("commit", "-q", "-m", "not text")
    done = repo.run("main", "binary")
    assert done.returncode == 1
    assert "Traceback" not in done.stderr
    assert f"refused: {check.LEDGER} is not text" in done.stderr


# --- there is no way back from a reservation ----------------------------------------------

#: No record is evidence of anything: nothing here can verify a review took place. The last
#: one is complete and plausible on purpose. Refusing only the sloppy ones would be the
#: false approval system this replaces.
_RECORDS = {
    "one-character-reason": [_record(reason="x")],
    "placeholder-reason": [_record(reason="TODO")],
    "blank-reason": [_record(reason="   ")],
    "arbitrary-review": [_record(reviewed_in="trust me")],
    "fabricated-review": [_record(reviewed_in="https://github.com/HeyderHesenov/x/pull/999999")],
    "no-review": [_record(reviewed_in="")],
    "figures-were-published": [_record(no_figures_published=False)],
    "another-reservation": [_record(previous_ledger_digest="b" * 64)],
    "another-identity": [_record(previous_identity=OTHER)],
    "unknown-fields": [{"approved": True}],
    "not-a-list": {"approved": True},
    "empty-list": [],
    "not-json": "not json",
    "complete-and-plausible": [_record()],
}


def _as_file(record) -> str:
    return record if isinstance(record, str) else json.dumps(record)


def test_a_reservation_never_goes_back_to_open_without_a_record():
    with pytest.raises(check.Refused, match="never goes back"):
        check.check_transition(RESERVED_TEXT, OPEN_TEXT)


@pytest.mark.parametrize("record", _RECORDS.values(), ids=_RECORDS.keys())
def test_no_record_takes_a_reservation_back_to_open(record):
    with pytest.raises(check.Refused, match="no recovery protocol"):
        check.check_transition(RESERVED_TEXT, OPEN_TEXT, _as_file(record))


@pytest.mark.parametrize("record", _RECORDS.values(), ids=_RECORDS.keys())
def test_no_record_takes_a_reservation_back_to_open_on_real_history(repo, record):
    repo.commit(RESERVED_TEXT, "a failed run, committed")
    base = repo.git("rev-parse", "HEAD").stdout.strip()
    (repo.path / check.RECOVERIES).write_text(_as_file(record))
    head = repo.commit(OPEN_TEXT, "the recovery")
    done = repo.run(base, head)
    assert done.returncode == 1
    assert "no recovery protocol" in done.stderr


@pytest.mark.parametrize(
    ("before", "after"),
    [
        pytest.param(OPEN_TEXT, OPEN_TEXT, id="open-unchanged"),
        pytest.param(OPEN_TEXT, RESERVED_TEXT, id="on-a-reservation"),
        pytest.param(RESERVED_TEXT, RESERVED_TEXT, id="reserved-unchanged"),
        pytest.param(RESERVED_TEXT, PUBLISHED_TEXT, id="on-a-publication"),
        pytest.param(PUBLISHED_TEXT, PUBLISHED_TEXT, id="on-a-release"),
        pytest.param(PUBLISHED_TEXT, OPEN_TEXT, id="to-undo-a-release"),
    ],
)
def test_a_record_is_refused_on_every_transition(before, after):
    """A trail with no process behind it would read as evidence, so it does not land at all."""
    with pytest.raises(check.Refused, match="no recovery protocol"):
        check.check_transition(before, after, json.dumps([_record()]))


def test_a_reservation_finishes_only_for_its_own_identity_and_date():
    assert check.check_transition(RESERVED_TEXT, PUBLISHED_TEXT)
    assert check.check_transition(RESERVED_TEXT, WITHHELD_TEXT)
    assert check.check_transition(RESERVED_TEXT, RESERVED_TEXT)
    with pytest.raises(check.Refused, match="own identity"):
        check.check_transition(RESERVED_TEXT, SECOND_TEXT)


@pytest.mark.parametrize(
    "flag", ["--force", "--reset", "--recover", "--allow-recovery", "--reopen", "--skip"]
)
def test_the_script_has_no_flag_that_reopens_a_reservation(repo, flag):
    repo.commit(RESERVED_TEXT, "a failed run, committed")
    base = repo.git("rev-parse", "HEAD").stdout.strip()
    head = repo.commit(OPEN_TEXT, "back to open")
    done = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--target", base, "--proposed", head, flag],
        cwd=repo.path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 2
    assert "allowed" not in done.stdout


def test_no_variable_changes_what_the_script_decides(repo):
    repo.commit(RESERVED_TEXT, "a failed run, committed")
    base = repo.git("rev-parse", "HEAD").stdout.strip()
    head = repo.commit(OPEN_TEXT, "back to open")
    names = ("FORCE", "RESET_RELEASE", "CHURNSENSE_RESET", "CHURNSENSE_ALLOW_RECOVERY", "CI")
    done = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--target", base, "--proposed", head],
        cwd=repo.path,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **dict.fromkeys(names, "1")},
    )
    assert done.returncode == 1
    assert "never goes back" in done.stderr


# --- as CI runs it, against real history --------------------------------------------------


class Repo:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args: str, check_: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=self.path, capture_output=True, text=True, check=check_
        )

    def commit(self, text: str | None, message: str = "change") -> str:
        ledger = self.path / check.LEDGER
        if text is None:
            ledger.unlink()
        else:
            ledger.parent.mkdir(parents=True, exist_ok=True)
            ledger.write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD").stdout.strip()

    def merged(self, target: str, head: str) -> subprocess.CompletedProcess:
        return self.git("merge-tree", "--write-tree", target, head, check_=False)

    def run(self, target: str, proposed: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "--target", target, "--proposed", proposed],
            cwd=self.path,
            capture_output=True,
            text=True,
            check=False,
        )


@pytest.fixture
def repo(tmp_path) -> Repo:
    made = Repo(tmp_path / "repo")
    made.commit(OPEN_TEXT, "the open ledger")
    return made


def test_a_release_on_a_branch_passes_against_open(repo):
    repo.git("checkout", "-q", "-b", "release")
    head = repo.commit(PUBLISHED_TEXT, "the release")
    done = repo.run("main", repo.merged("main", head).stdout.strip())
    assert done.returncode == 0, done.stderr
    assert "open -> published" in done.stdout


def test_two_pull_requests_from_one_base_cannot_both_release(repo):
    """Each was green on its own base. Judged against the branch as it is now, one is not."""
    repo.git("checkout", "-q", "-b", "first")
    first = repo.commit(PUBLISHED_TEXT, "a release here")
    repo.git("checkout", "-q", "main")
    repo.git("checkout", "-q", "-b", "second")
    second = repo.commit(SECOND_TEXT, "a release elsewhere")
    repo.git("checkout", "-q", "main")

    for head in (first, second):
        tree = repo.merged("main", head).stdout.strip()
        assert repo.run("main", tree).returncode == 0, "each passes before the other lands"

    repo.git("merge", "-q", "--no-ff", "--no-edit", first)
    merge = repo.merged("main", second)
    assert merge.returncode != 0, "the two ledgers conflict, so the workflow refuses outright"

    # Fixing the conflict by taking the second ledger is a new commit, judged afresh.
    repo.git("checkout", "-q", "second")
    repo.git("merge", "-q", "--no-commit", "main", check_=False)
    (repo.path / check.LEDGER).write_text(SECOND_TEXT)
    repo.git("add", "-A")
    repo.git("commit", "-q", "-m", "resolve by taking mine")
    resolved = repo.git("rev-parse", "HEAD").stdout.strip()
    done = repo.run("main", repo.merged("main", resolved).stdout.strip())
    assert done.returncode == 1
    assert "immutable" in done.stderr


def test_an_old_open_ledger_cannot_come_back_through_a_conflict_resolution(repo):
    repo.git("checkout", "-q", "-b", "old")
    repo.commit(RESERVED_TEXT, "work from before the release")
    repo.git("checkout", "-q", "main")
    repo.commit(PUBLISHED_TEXT, "the release lands")
    repo.git("checkout", "-q", "old")
    repo.git("merge", "-q", "--no-commit", "main", check_=False)
    (repo.path / check.LEDGER).write_text(OPEN_TEXT)
    repo.git("add", "-A")
    repo.git("commit", "-q", "-m", "resolve by taking the old file")
    head = repo.git("rev-parse", "HEAD").stdout.strip()
    done = repo.run("main", repo.merged("main", head).stdout.strip())
    assert done.returncode == 1
    assert "immutable" in done.stderr


def test_a_stale_pull_request_that_leaves_the_ledger_alone_is_judged_by_the_merge(repo):
    """Its own file still says open. What it would leave says published, which is fine."""
    repo.git("checkout", "-q", "-b", "unrelated")
    (repo.path / "note.txt").write_text("an unrelated change\n")
    repo.git("add", "note.txt")
    repo.git("commit", "-q", "-m", "unrelated")
    head = repo.git("rev-parse", "HEAD").stdout.strip()
    repo.git("checkout", "-q", "main")
    repo.commit(PUBLISHED_TEXT, "the release lands")
    done = repo.run("main", repo.merged("main", head).stdout.strip())
    assert done.returncode == 0, done.stderr
    assert "unchanged, published" in done.stdout
    # Compared alone, the same stale branch would read as a rollback: that is why it is merged.
    assert repo.run("main", head).returncode == 1


def test_deleting_the_ledger_is_refused(repo):
    head = repo.commit(None, "delete it")
    done = repo.run("HEAD~1", head)
    assert done.returncode == 1
    assert "deleted" in done.stderr


def test_a_bad_revision_is_refused_and_nothing_is_said_of_figures(repo):
    repo.git("checkout", "-q", "-b", "bad")
    head = repo.commit(PUBLISHED_TEXT, "a release")
    repo.git("checkout", "-q", "main")
    assert repo.run("no-such-branch", head).returncode == 1
    repo.commit(SECOND_TEXT, "another")
    done = repo.run("HEAD~1", head)
    assert done.returncode == 0, "an allowed change says so without quoting it"
    for figure in ("500", "0.4286", "0.36"):
        assert figure not in done.stdout + done.stderr


@pytest.mark.parametrize("text", payloads.INVALID.values(), ids=payloads.INVALID.keys())
def test_a_rejected_publication_fails_the_real_command_and_quotes_nothing(repo, text):
    """On real history, as CI runs it: exit 1, the rule named, nothing of the payload repeated."""
    repo.git("checkout", "-q", "-b", "release")
    head = repo.commit(text, "a publication")
    done = repo.run("main", repo.merged("main", head).stdout.strip())
    assert done.returncode == 1
    assert done.stderr.startswith("refused: ")
    assert "Traceback" not in done.stderr
    assert str(payloads.SENTINEL) not in done.stderr
    assert not re.search(r"\d\.\d", done.stderr), "no figure"
    for key in payloads.FORBIDDEN_FIGURES:
        assert key not in done.stderr, "not even a field's name: it could hold a value"


def test_the_script_has_no_way_around_itself():
    source = SCRIPT.read_text()
    assert "environ" not in source and "getenv" not in source
    parser_flags = set(re.findall(r'add_argument\(\s*"(--[a-z-]+)"', source))
    assert parser_flags == {"--target", "--proposed"}
    assert not re.search(
        r"\.write|open\(|unlink|make evaluate",
        source.replace("It never runs an evaluation and never writes.", ""),
    )


# --- the workflow's own shell step, on real history ---------------------------------------


def _step(workflow: dict) -> str:
    steps = workflow["jobs"]["held-out-release-governance"]["steps"]
    return next(s["run"] for s in steps if "run" in s)


def _as_ci(repo: Repo, workflow: dict, tmp_path: Path, **event: str) -> subprocess.CompletedProcess:
    """The step exactly as written, with what GitHub would put in its environment."""
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", "-c", _step(workflow)],
        cwd=repo.path,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            "RUNNER_TEMP": str(runner_temp),
            "SERVER_URL": f"file://{repo.path.parent}",
            "REPOSITORY": repo.path.name,
            **event,
        },
    )


@pytest.fixture
def repo_with_rules(repo) -> Repo:
    target = repo.path / "scripts" / "check_release_ledger.py"
    target.parent.mkdir()
    target.write_text(SCRIPT.read_text())
    repo.git("add", "-A")
    repo.git("commit", "-q", "-m", "the rules")
    return repo


def _pull_request(head: str) -> dict:
    return {"EVENT": "pull_request", "PR_BASE_REF": "main", "PR_HEAD_SHA": head}


def test_the_step_passes_an_ordinary_release(repo_with_rules, workflow, tmp_path):
    repo = repo_with_rules
    repo.git("checkout", "-q", "-b", "release")
    head = repo.commit(PUBLISHED_TEXT, "the release")
    repo.git("checkout", "-q", "main")
    done = _as_ci(repo, workflow, tmp_path, **_pull_request(head))
    assert done.returncode == 0, done.stderr
    assert "allowed: open -> published" in done.stdout


def test_the_step_refuses_a_conflict_with_the_target(repo_with_rules, workflow, tmp_path):
    repo = repo_with_rules
    repo.git("checkout", "-q", "-b", "second")
    head = repo.commit(SECOND_TEXT, "a release elsewhere")
    repo.git("checkout", "-q", "main")
    repo.commit(PUBLISHED_TEXT, "the first release lands")
    done = _as_ci(repo, workflow, tmp_path, **_pull_request(head))
    assert done.returncode == 1
    assert "conflicts" in done.stderr


def test_the_step_judges_an_ordinary_change_by_the_copy_on_the_target(
    repo_with_rules, workflow, tmp_path
):
    """Catches an edit to the script in the same change. See the next test for how far it goes."""
    repo = repo_with_rules
    repo.commit(PUBLISHED_TEXT, "the first release lands")
    repo.git("checkout", "-q", "-b", "sneaky")
    (repo.path / "scripts" / "check_release_ledger.py").write_text("print('allowed')\n")
    head = repo.commit(SECOND_TEXT, "a second release, and a kinder check")
    repo.git("checkout", "-q", "main")
    done = _as_ci(repo, workflow, tmp_path, **_pull_request(head))
    assert done.returncode == 1
    assert "immutable" in done.stderr


def test_known_limit_a_merged_change_can_weaken_the_check_for_the_next_one(
    repo_with_rules, workflow, tmp_path
):
    """The accepted limit of ``pull_request``, in code: why this is not a security boundary.

    The first change touches no ledger, so it passes, and only a person reading it can stop it.
    Once merged, the target's copy is the kinder one. Should this test ever fail, the limit has
    gone and ``SECURITY.md``, "Its limits", has to say so. The workflow file is a second route,
    since GitHub takes it from the pull request itself; that one cannot be run here.
    """
    repo = repo_with_rules
    repo.commit(PUBLISHED_TEXT, "the first release lands")
    repo.git("checkout", "-q", "-b", "weaken")
    (repo.path / "scripts" / "check_release_ledger.py").write_text("print('allowed')\n")
    repo.git("add", "-A")
    repo.git("commit", "-q", "-m", "a kinder check, and nothing else")
    weaken = repo.git("rev-parse", "HEAD").stdout.strip()
    repo.git("checkout", "-q", "main")
    assert _as_ci(repo, workflow, tmp_path, **_pull_request(weaken)).returncode == 0
    repo.git("merge", "-q", "--no-ff", "--no-edit", weaken)

    repo.git("checkout", "-q", "-b", "second")
    head = repo.commit(SECOND_TEXT, "a second release")
    repo.git("checkout", "-q", "main")
    done = _as_ci(repo, workflow, tmp_path, **_pull_request(head))
    assert done.returncode == 0, "if this now fails, correct what the documents say"


@pytest.mark.parametrize(
    "name", ["forbidden-brier", "fn-19", "threshold-other-than-the-identity", "identity-not-hex"]
)
def test_the_step_refuses_a_rejected_publication(repo_with_rules, workflow, tmp_path, name):
    repo = repo_with_rules
    repo.git("checkout", "-q", "-b", "release")
    head = repo.commit(payloads.INVALID[name], "a publication")
    repo.git("checkout", "-q", "main")
    done = _as_ci(repo, workflow, tmp_path, **_pull_request(head))
    assert done.returncode == 1
    assert "refused:" in done.stderr


def test_the_step_lets_a_committed_reservation_land(repo_with_rules, workflow, tmp_path):
    """How a failed run is made durable against a local ``git restore``."""
    repo = repo_with_rules
    repo.git("checkout", "-q", "-b", "reservation")
    head = repo.commit(RESERVED_TEXT, "a failed run, committed")
    repo.git("checkout", "-q", "main")
    done = _as_ci(repo, workflow, tmp_path, **_pull_request(head))
    assert done.returncode == 0, done.stderr
    assert "allowed: open -> reserved" in done.stdout


def test_the_step_uses_what_a_merge_queue_will_merge(repo_with_rules, workflow, tmp_path):
    repo = repo_with_rules
    base = repo.commit(PUBLISHED_TEXT, "the first release lands")
    head = repo.commit(OPEN_TEXT, "an old file comes back")
    event = {"EVENT": "merge_group", "GROUP_BASE_SHA": base, "GROUP_HEAD_SHA": head}
    done = _as_ci(repo, workflow, tmp_path, **event)
    assert done.returncode == 1
    assert "immutable" in done.stderr


def test_the_change_that_first_adds_the_rules_runs_its_own_copy(repo, workflow, tmp_path):
    """Main has no rules yet: the bootstrap, once, and only for an ``open`` ledger."""
    target = repo.path / "scripts" / "check_release_ledger.py"
    repo.git("checkout", "-q", "-b", "add-the-check")
    target.parent.mkdir()
    target.write_text(SCRIPT.read_text())
    head = repo.commit(OPEN_TEXT, "the rules arrive")
    done = _as_ci(repo, workflow, tmp_path, **_pull_request(head))
    assert done.returncode == 0, done.stderr


# --- the workflow -------------------------------------------------------------------------


def _triggers(workflow: dict) -> dict:
    # YAML reads a bare ``on`` as the boolean true.
    return workflow.get("on", workflow.get(True))


def test_the_check_has_the_name_the_ruleset_will_require(workflow):
    job = workflow["jobs"]["held-out-release-governance"]
    assert job["name"] == "held-out-release-governance"


def test_it_runs_where_it_must_and_can_always_report(workflow):
    triggers = _triggers(workflow)
    assert set(triggers) == {"pull_request", "merge_group"}
    assert triggers["pull_request"] == {"branches": ["main"]}, "no path filter: it must report"


@pytest.mark.parametrize(
    "event",
    ["pull_request_target", "workflow_run", "issue_comment", "repository_dispatch", "schedule"],
)
def test_no_privileged_event_runs_it(workflow, event):
    """The approved decision: no trigger that holds privileges and fetches a change's code."""
    assert event not in _triggers(workflow)
    assert event not in WORKFLOW.read_text()


def test_it_can_read_and_nothing_else(workflow):
    assert workflow["permissions"] == {"contents": "read"}
    text = WORKFLOW.read_text()
    assert "pull_request_target" not in text
    assert "secrets." not in text
    steps = workflow["jobs"]["held-out-release-governance"]["steps"]
    checkout = next(s for s in steps if "uses" in s and "checkout" in s["uses"])
    assert checkout["with"]["persist-credentials"] is False


def test_every_action_is_pinned_to_a_full_sha(workflow):
    steps = workflow["jobs"]["held-out-release-governance"]["steps"]
    uses = [s["uses"] for s in steps if "uses" in s]
    assert uses
    for action in uses:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", action), action


def test_no_event_value_reaches_a_shell_except_through_the_environment(workflow):
    for step in workflow["jobs"]["held-out-release-governance"]["steps"]:
        assert "${{" not in step.get("run", ""), step.get("name")


def test_it_evaluates_nothing_and_needs_no_data(workflow):
    text = " ".join(
        s.get("run", "") for s in workflow["jobs"]["held-out-release-governance"]["steps"]
    )
    for word in ("make ", "pip install", "churnsense", "data/", "artifacts/"):
        assert word not in text, word


def test_it_judges_the_merge_with_the_target_as_it_is_now(workflow):
    run = next(
        s["run"] for s in workflow["jobs"]["held-out-release-governance"]["steps"] if "run" in s
    )
    assert "git fetch" in run and "merge-tree --write-tree" in run
    assert "GROUP_BASE_SHA" in run and "GROUP_HEAD_SHA" in run
    assert 'git show "$target:scripts/check_release_ledger.py"' in run, "the target's own rules"
