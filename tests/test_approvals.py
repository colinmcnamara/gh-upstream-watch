"""0.3.1: a release run waiting for your approval (a protected environment such as pypi) alerts
once, even on a seed run, because it is waiting on you right now."""
import json

from conftest import FIXTURES

from gh_upstream_watch import cli, core, github

R = "acme/widgets"
RUNS = f"repos/{R}/actions/runs?per_page=20&status=waiting"
PENDING = f"repos/{R}/actions/runs/5/pending_deployments"
RUN = {"id": 5, "head_branch": "v1.2.3", "name": "release", "event": "push",
       "html_url": f"https://github.com/{R}/actions/runs/5"}


def pending(can=True, name="pypi"):
    return [{"environment": {"id": 1, "name": name}, "current_user_can_approve": can}]


def test_a_waiting_approval_alerts_once(fake):
    fake.responses.update({RUNS: {"workflow_runs": [RUN]}, PENDING: pending()})
    seen, live = {}, set()
    got = core.approval_asks(seen, R, 1.79e9, live)
    assert len(got) == 1 and got[0]["kind"] == "approval" and got[0]["key"] == f"{R} run 5"
    assert got[0]["title"] == f"{R} v1.2.3" and "waiting for your approval: pypi" in got[0]["message"]
    assert got[0]["url"] == RUN["html_url"] and f"{R} run 5" in live
    assert core.approval_asks(seen, R, 1.79e9, set()) == [], "once per run"
    assert "approval" in core.ACTION_KINDS


def test_only_what_you_can_approve(fake):
    fake.responses.update({RUNS: {"workflow_runs": [RUN]}, PENDING: pending(can=False)})
    assert core.approval_asks({}, R, 1.79e9, set()) == []
    fake.responses[PENDING] = []
    assert core.approval_asks({}, R, 1.79e9, set()) == [], "nothing pending (a wait timer, say)"


def test_a_failed_lookup_is_unknown_not_quiet(fake):
    fake.responses[RUNS] = {"workflow_runs": [RUN]}  # PENDING missing: a 404
    try:
        core.approval_asks({}, R, 1.79e9, set())
    except github.GHError:
        return
    raise AssertionError("a failed pending lookup must not read as nothing waiting")


def test_the_run_alerts_even_while_seeding(fake, tmp_path, capsys):
    fake.load("run1")
    fake.responses.update({RUNS: {"workflow_runs": [RUN]}, PENDING: pending()})
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"approvals": [R]}))
    args = ["--config", str(cfg), "--state", str(tmp_path / "s.json"), "--repos", R,
            "--packs-dir", str(FIXTURES / "packs"), "--notify", "none", "--json"]
    assert cli.main(args) == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert [x["kind"] for x in lines] == ["approval"], "the seed run holds everything else back"
    assert cli.main(args) == 0
    assert "approval" not in capsys.readouterr().out, "once"
    st = json.loads((tmp_path / "s.json").read_text())
    assert f"{R} run 5" in st["approvals"]["seen"]


def test_approvals_is_a_list_of_repos(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"approvals": "acme/widgets"}))
    assert cli.main(["--config", str(cfg), "--state", str(tmp_path / "s.json"), "--repos", R, "status"]) == 2


def test_review_fixes(fake):
    """Opus review: a re-run waits on you again (same run id, new attempt); more waiting runs than one
    page is unknown, not dropped; a null environment does not fail the repo."""
    fake.responses.update({RUNS: {"total_count": 1, "workflow_runs": [dict(RUN, run_attempt=1)]}, PENDING: pending()})
    seen = {}
    assert len(core.approval_asks(seen, R, 1.79e9, set())) == 1
    fake.responses[RUNS] = {"total_count": 1, "workflow_runs": [dict(RUN, run_attempt=2)]}
    again = core.approval_asks(seen, R, 1.79e9, set())
    assert len(again) == 1 and again[0]["key"] == f"{R} run 5.2", "a re-run is a new wait"
    fake.responses[RUNS] = {"total_count": 25, "workflow_runs": [RUN]}
    try:
        core.approval_asks({}, R, 1.79e9, set())
        raise AssertionError("25 waiting, 1 read: must be unknown")
    except github.Incomplete:
        pass
    fake.responses[RUNS] = {"total_count": 1, "workflow_runs": [RUN]}
    fake.responses[PENDING] = [{"environment": None, "current_user_can_approve": True}]
    assert "waiting for your approval: ?" in core.approval_asks({}, R, 1.79e9, set())[0]["message"]


def test_a_reply_that_is_not_a_list_is_unknown(fake):
    """Codex review: a null or object reply is not "nothing pending"."""
    fake.responses[RUNS] = {"total_count": 1, "workflow_runs": [RUN]}
    for bad in (None, {"message": "odd"}):
        fake.responses[PENDING] = bad
        try:
            core.approval_asks({}, R, 1.79e9, set())
            raise AssertionError(f"{bad!r} read as nothing pending")
        except github.GHError:
            pass
