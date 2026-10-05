"""State: atomic saves, one writer, corrupt-file recovery, bounded seen stores, v0 migration."""
import json
import os

import pytest
from conftest import FIXTURES

from gh_upstream_watch import state


def test_save_is_atomic_and_round_trips(tmp_path):
    p = str(tmp_path / "s" / "state.json")
    st = state.empty()
    st["items"]["acme/widgets#1"] = {"state": "open"}
    state.save(p, st)
    assert state.load(p) == st
    assert os.listdir(tmp_path / "s") == ["state.json"], "no temp file left behind"


def test_second_writer_is_refused(tmp_path):
    p = str(tmp_path / "state.json")
    with state.lock(p), pytest.raises(state.Locked), state.lock(p):
        pass
    with state.lock(p):  # released after the first run ends
        pass


@pytest.mark.parametrize("junk", ['{"schema": 1, "items": {', "", "[1, 2]"])
def test_corrupt_or_partial_state_is_quarantined_and_reseeded(tmp_path, junk, capsys):
    p = tmp_path / "state.json"
    p.write_text(junk)
    st = state.load(str(p))
    assert st == state.empty() and st["seeded"] is False, "a fresh state re-seeds quietly: no alert flood"
    assert not p.exists() and any(f.name.startswith("state.json.corrupt-") for f in tmp_path.iterdir())
    assert "re-seeding quietly" in capsys.readouterr().err


def test_newer_schema_is_refused(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"schema": 99}))
    with pytest.raises(state.NewerState, match="Upgrade"):
        state.load(str(p))
    assert json.loads(p.read_text()) == {"schema": 99}, "kept, not quarantined"


def test_prune_after_retention_keeps_live_ids():
    now = 1.8e9
    st = state.empty()
    st["notifications"]["seen"] = {"old": "2020-01-01T00:00:00Z", "old-but-live": "2020-01-01T00:00:00Z",
                                   "recent": "2027-01-15T00:00:00Z"}
    st["claimable"]["seen"] = {"acme/widgets#1": now - 40 * 86400, "acme/widgets#2": now - 86400}
    st["slack"] = {"seen": {"C1:1.0": now - 40 * 86400}}
    state.prune(st, now, 30, live={"old-but-live"})
    assert set(st["notifications"]["seen"]) == {"old-but-live", "recent"}
    assert set(st["claimable"]["seen"]) == {"acme/widgets#2"}
    assert st["slack"]["seen"] == {}


def test_migrate_v0_keeps_fingerprints_and_seen_ids():
    v0 = json.loads((FIXTURES / "state_v0.json").read_text())
    st = state.migrate_v0(v0, 1.7e9, claim_repo="acme/widgets")
    assert st["schema"] == 1 and st["seeded"] is True, "migration never re-seeds"
    assert set(st["items"]) == {"acme/widgets#388", "acme/widgets#390", "acme/widgets#392", "acme/widgets#395"}
    assert st["items"]["acme/widgets#388"]["gates"] == {"accept": {"by": "", "done": True}}
    assert st["items"]["acme/widgets#390"]["gates"] == {}
    assert "accepted" not in st["items"]["acme/widgets#390"]
    assert st["items"]["acme/widgets#390"]["xrefs"] == v0["acme/widgets#390"]["xrefs"]
    assert st["notifications"]["seen"] == {"1001": "2026-01-05T10:00:00Z"}
    assert st["claimable"]["seen"] == {"acme/widgets#3613": 1.7e9}
    assert st["slack"]["seen"] == {"#general:1767000000.000100": 1767000000.0001} and st["slack"]["seeded"]


def test_v0_file_at_the_state_path_is_migrated_on_load(tmp_path):
    p = tmp_path / "state.json"
    p.write_text((FIXTURES / "state_v0.json").read_text())
    assert state.load(str(p))["schema"] == 1


# --- seeding ---------------------------------------------------------------------------------------

A = {"title": "a"}


def seeding(st, alerts, sources, failed=(), old=(), extras=()):
    return state.hold_until_seeded(st, alerts, set(sources), set(failed), {k: {} for k in old}, list(extras))


def test_a_new_state_holds_first_sight_and_seeds_what_completed():
    st = state.empty()
    send, held, newly = seeding(st, [("repo:a/b", A), ("notifications", A), ("live", A)],
                                ["repo:a/b", "notifications"], failed=["notifications"])
    assert send == [A] and len(held) == 2 and newly == {"repo:a/b"}
    assert st["seeded_sources"] == ["repo:a/b"] and st["seeded"] is False


def test_a_failing_source_holds_back_only_itself():
    st = dict(state.empty(), seeded_sources=["repo:a/b"])
    send, held, _ = seeding(st, [("repo:a/b", A), ("notifications", A)], ["repo:a/b", "notifications"],
                            failed=["notifications"])
    assert len(send) == 1 and len(held) == 1 and st["seeded"] is False
    seeding(st, [], ["repo:a/b", "notifications"])
    assert st["seeded"] is True, "seeded once every source has completed"


def test_slack_and_live_alerts_are_never_held():
    send, held, _ = seeding(state.empty(), [("slack", A), ("live", A)], ["repo:a/b"], failed=["repo:a/b"])
    assert len(send) == 2 and held == []


def test_upgraded_state_seeds_what_it_watched_but_not_a_new_repo():
    st = dict(state.empty(), seeded=True)  # 0.1.0: no seeded_sources
    send, held, newly = seeding(st, [("repo:a/new", A), ("repo:a/b", A)], ["repo:a/b", "repo:a/new", "notifications"],
                                old=["a/b#1"])
    assert send == [A] and held == [A] and newly == {"repo:a/new"}


def test_an_extra_new_since_the_upgrade_still_seeds():
    st = dict(state.empty(), seeded=True)
    _, held, _ = seeding(st, [("extra:a/b#9", A)], ["repo:a/b", "extra:a/b#9"], old=["a/b#1"], extras=["a/b#9"])
    assert held == [A]


def test_removing_a_source_cannot_leave_the_flag_stuck():
    st = dict(state.empty(), seeded_sources=["repo:a/b"])
    seeding(st, [], ["repo:a/b"])
    assert st["seeded"] is True
