"""0.3.2, from real data: `done` clears an item from "waiting on you" until something new happens;
packs name CI checks that fail by design (vLLM's pre-run-check on a first-time contributor's PR);
packs quiet bot label flips (semantic-router's pr/needs-review and pr/needs-rebase)."""
import json
import time

import pytest

from gh_upstream_watch import cli, core, packs, state

ME = "octocat"
R = "acme/widgets"
NOW = 1.79e9


def pack(**extra):
    return packs.validate(dict({"id": "t", "repos": [R]}, **extra), "test")


def rules(**extra):
    return packs.for_repo([pack(**extra)], R)


def test_pack_keys_are_validated():
    p = pack(ci_by_design={"pre-run-check": "red until a maintainer runs CI"}, quiet_labels=["^pr/needs-"])
    assert p["ci_by_design"]["pre-run-check"]
    with pytest.raises(packs.PackError):
        pack(ci_by_design=["pre-run-check"])
    with pytest.raises(packs.PackError):
        pack(quiet_labels=["("])
    eff = rules(quiet_labels=["^pr/needs-"], ci_by_design={"x": "y"})
    assert eff["ci_by_design"] == {"x": "y"} and eff["quiet_labels"][0].search("pr/needs-review")


def test_bundled_packs_know_vllm_and_semantic_router():
    loaded = packs.load()
    assert "pre-run-check" in packs.for_repo(loaded, "vllm-project/vllm")["ci_by_design"]
    sr = packs.for_repo(loaded, "vllm-project/semantic-router")
    assert any(r.search("pr/needs-rebase") for r in sr["quiet_labels"])
    assert not any(r.search("accepted") for r in sr["quiet_labels"]), "real labels still alert"


base = dict(title="t", url=f"https://github.com/{R}/pull/7", state="open", labels=[], assignees=[], human_comments=0,
            gates={}, author=ME, reviews=[], merged=False)


def changes(old, new, eff):
    return [(k, m) for k, m, _ in core.changes(old, new, ME, eff)]


def test_quiet_label_flips_do_not_alert():
    eff = rules(quiet_labels=["^pr/needs-"])
    assert changes({**base, "labels": ["pr/needs-review"]}, {**base, "labels": ["pr/needs-rebase"]}, eff) == []
    assert changes({**base, "labels": ["pr/needs-review"]}, {**base, "labels": ["pr/needs-rebase", "bug"]}, eff) == \
        [("labels", "labels: +bug")], "only the real change is shown"


def test_a_check_red_by_design_waits_on_a_maintainer():
    eff = rules(ci_by_design={"pre-run-check": "red by design for first-time contributors"})
    pend = {**base, "ci": {"state": "pending", "sha": "a"}}
    gated = {**base, "ci": {"state": "failure", "failing": ["pre-run-check"], "sha": "a"}}
    got = changes(pend, gated, eff)
    assert got[0][0] == "ci_waiting" and "pre-run-check" in got[0][1] and "maintainer" in got[0][1]
    real = {**base, "ci": {"state": "failure", "failing": ["pre-run-check", "unit"], "sha": "a"}}
    assert changes(gated, real, eff) == [("ci_failed", "CI FAILED: unit")], "the real failure, not the gate"
    assert changes(gated, dict(gated), eff) == []


def iso(days_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - days_ago * 86400))


def save(tmp_path, items, done=None):
    st = state.empty()
    st.update(items=items, last_complete=NOW - 60, seeded=True, login=ME)
    if done is not None:
        st["done"] = done
    p = tmp_path / "s.json"
    state.save(str(p), st)
    return ["--state", str(p), "--repos", R]


def test_inbox_puts_a_by_design_check_under_them(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli.time, "time", lambda: NOW)
    pdir = tmp_path / "packs"
    pdir.mkdir()
    (pdir / "t.json").write_text(json.dumps({"id": "t", "repos": [R], "ci_by_design": {"pre-run-check": "red until a maintainer runs CI"}}))
    item = dict(state="open", url="u", author=ME, pr=True, created_at=iso(2))
    args = save(tmp_path, {f"{R}#1": dict(item, title="Gated", ci={"state": "failure", "failing": ["pre-run-check"]}),
                           f"{R}#2": dict(item, title="Red", ci={"state": "failure", "failing": ["pre-run-check", "unit"]})})
    assert cli.main(["inbox", *args, "--packs-dir", str(pdir)]) == 0
    you, them = capsys.readouterr().out.split("waiting on them")
    assert f"{R}#1 " in them and "pre-run-check: red until a maintainer runs CI" in them
    assert f"{R}#2 " in you and "CI FAILED: unit" in you and "pre-run-check" not in you.split(f"{R}#2 ")[1].split("\n")[0]


def test_done_clears_an_item_until_something_new(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli.time, "time", lambda: NOW)
    key = f"{R}#5"
    item = dict(state="open", title="Thanks", url="u", author="lead", mention_at=iso(1), my_at=iso(3))
    args = save(tmp_path, {key: item})
    assert cli.main(["done", key, *args]) == 0
    assert "done" in capsys.readouterr().out
    assert cli.main(["inbox", *args]) == 0
    assert key not in capsys.readouterr().out, "handled: gone from the inbox"
    st = json.loads((tmp_path / "s.json").read_text())
    st["items"][key]["mention_at"] = iso(-1)  # a newer mention, after `done`
    state.save(str(tmp_path / "s.json"), st)
    assert cli.main(["inbox", *args]) == 0
    assert key in capsys.readouterr().out, "something new: back in the inbox"


def test_done_refuses_what_is_not_waiting(tmp_path, capsys):
    args = save(tmp_path, {f"{R}#6": dict(state="open", title="t", url="u", author="lead")})
    assert cli.main(["done", f"{R}#6", *args]) == 2
    assert cli.main(["done", f"{R}#99", *args]) == 2
    assert "not waiting on you" in capsys.readouterr().err


def test_any_alert_on_an_item_undoes_done(fake, tmp_path, monkeypatch):
    """Opus review: a new CI failure on a new commit, a conflict that comes back, a reopen all keep the
    reason text, so `done` must end on any alert about the item, not on the text alone."""
    from conftest import FIXTURES
    fake.load("run1")
    args = ["--state", str(tmp_path / "s.json"), "--repos", R, "--packs-dir", str(FIXTURES / "packs"), "--notify", "none"]
    assert cli.main(args) == 0  # seed
    st = json.loads((tmp_path / "s.json").read_text())
    st["done"] = {f"{R}#388": {"at": "2000-01-01T00:00:00Z", "why": ["x"]}, f"{R}#390": {"at": "2000-01-01T00:00:00Z", "why": ["x"]}}
    state.save(str(tmp_path / "s.json"), st)
    fake.load("run2")  # #388 is reopened and #390 gets /accept: both alert
    assert cli.main(args) == 0
    assert json.loads((tmp_path / "s.json").read_text())["done"] == {}


def test_done_is_stamped_with_the_data_you_saw(tmp_path, monkeypatch):
    """Opus review: a mention that arrived after the last run but before `done` was never seen, so it
    must still show. `done` uses the last complete run's time, not the clock."""
    monkeypatch.setattr(cli.time, "time", lambda: NOW)
    key = f"{R}#5"
    args = save(tmp_path, {key: dict(state="open", title="t", url="u", author="lead", mention_at=iso(1), my_at=iso(3))})
    assert cli.main(["done", key, *args]) == 0
    at = json.loads((tmp_path / "s.json").read_text())["done"][key]["at"]
    assert at == time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - 60)), "last_complete, not now"


def test_codex_review_fixes():
    """A rebase the bot asks for still alerts (once, when added); a new by-design check is news."""
    sr = packs.for_repo(packs.load(), "vllm-project/semantic-router")
    review, rebase = {**base, "labels": ["pr/needs-review"]}, {**base, "labels": ["pr/needs-rebase"]}
    assert [k for k, _ in changes(review, rebase, sr)] == ["label_rule"], "added: one clear alert"
    assert changes(rebase, review, sr) == [], "removed: quiet"
    eff = rules(ci_by_design={"gate-a": "a", "gate-b": "b"})
    a = {**base, "ci": {"state": "failure", "failing": ["gate-a"], "sha": "1"}}
    ab = {**base, "ci": {"state": "failure", "failing": ["gate-a", "gate-b"], "sha": "1"}}
    assert [k for k, _ in changes(a, ab, eff)] == ["ci_waiting"], "a newly gated check is news"
    assert changes(a, {**a, "ci": dict(a["ci"], sha="2")}, eff) == [], "the same gate on a new push is not"
