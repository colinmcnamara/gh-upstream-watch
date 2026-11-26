"""semantic-router#5574: a maintainer accepted the issue, then someone else commented /assign and was
assigned. The inbox kept saying "ACCEPTED by @...: your move" for six days. Taken by someone else, it
waits on them; assigned to you, it is still yours."""
from gh_upstream_watch import cli, core, packs, state

ME = "octocat"
SR = "vllm-project/semantic-router"
SR_RULES = packs.for_repo(packs.load([]), SR)  # the bundled pack
KEY = f"{SR}#9"


def accepted_issue(fake, tmp_path, assignees):
    """Your issue, accepted by a MEMBER (the bot's `accepted` label passes the gate), assigned to `assignees`;
    fingerprinted through the fake GitHub and saved as the last run would save it."""
    fake.responses.update({
        f"repos/{SR}/issues/9": {"number": 9, "title": "Bug", "html_url": f"https://github.com/{SR}/issues/9",
                                 "state": "open", "comments": 2, "labels": [{"name": "accepted"}],
                                 "assignees": [{"login": a} for a in assignees], "user": {"login": ME},
                                 "created_at": "2026-10-04T19:04:49Z", "body": ""},
        f"repos/{SR}/issues/9/comments?page=1&per_page=100": [
            {"id": 1, "user": {"login": "maint"}, "author_association": "MEMBER", "body": "/accept",
             "created_at": "2026-10-06T09:00:14Z"},
            {"id": 2, "user": {"login": "monalisa"}, "author_association": "NONE", "body": "/assign",
             "created_at": "2026-10-07T10:00:00Z"}],
        f"repos/{SR}/issues/9/timeline?page=1&per_page=100": [],
    })
    st = state.empty()
    st.update(items={KEY: core.fingerprint(SR, 9, ME, SR_RULES)}, seeded=True, login=ME)
    path = tmp_path / "s.json"
    state.save(str(path), st)
    return ["--state", str(path), "--repos", SR]


def test_an_accepted_issue_taken_by_someone_else_is_not_your_move(fake, tmp_path, capsys):
    args = accepted_issue(fake, tmp_path, ["monalisa", "mona2"])
    assert cli.main(["inbox", *args]) == 0
    out = capsys.readouterr().out
    you, them = out.split("waiting on them")
    assert "your move" not in out
    assert KEY not in you
    assert f"{KEY} Bug: ACCEPTED by @maint; taken by @mona2, @monalisa; waiting" in them
    assert cli.main(["done", KEY, *args]) == 2, "nothing waits on you, so nothing to mark done"


def test_an_accepted_issue_assigned_to_you_stays_your_move(fake, tmp_path, capsys):
    args = accepted_issue(fake, tmp_path, [ME])
    assert cli.main(["inbox", *args]) == 0
    you = capsys.readouterr().out.split("waiting on them")[0]
    assert f"{KEY} Bug: ACCEPTED by @maint: your move" in you
