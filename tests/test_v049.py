"""Two misses in what counts as naming you.

agentic-ai-usecases-requirements#87: the editor replied to both of the user's inline review comments
on their PR at 10:38 UTC. The replies carry no @user and GitHub's notification for them is not a
mention, so `explain` said "0 naming you" and nothing alerted; the user found them by polling.

semantic-router#2967: the weekly workgroup outreach @-names every member. It showed in `inbox` as an
"unanswered mention" though it asks nobody in particular."""
from gh_upstream_watch import cli, core, packs, state

ME = "octocat"
R = "ietf-wg-agentproto/agentic-ai-usecases-requirements"
RULES = packs.for_repo(packs.load([]), R)
KEY = f"{R}#87"

MY_ROOTS = [{"id": 4713270513, "user": {"login": ME}, "in_reply_to_id": None, "created_at": "2026-11-26T20:52:16Z", "body": "4.6 vs 4.7"},
            {"id": 4713270519, "user": {"login": ME}, "in_reply_to_id": None, "created_at": "2026-11-26T20:52:16Z", "body": "same in 4.7"}]
REPLIES = [{"id": 4718337521, "user": {"login": "tireddy2"}, "in_reply_to_id": 4713270513, "created_at": "2026-11-27T10:38:28Z", "body": "Updated"},
           {"id": 4718345517, "user": {"login": "tireddy2"}, "in_reply_to_id": 4713270519, "created_at": "2026-11-27T10:39:24Z", "body": "Fixed"}]
MY_ANSWERS = [{"id": 4719694963, "user": {"login": ME}, "in_reply_to_id": 4713270513, "created_at": "2026-11-27T13:00:47Z", "body": "Thanks"}]


def review(rid, login, at):
    return {"id": rid, "user": {"login": login}, "state": "COMMENTED", "submitted_at": at, "body": "",
            "author_association": "MEMBER" if login != ME else "NONE"}


def pr87(fake, inline, reviews):
    """The editor's open PR #87, which the user reviewed, as fingerprint() reads it."""
    fake.responses.update({
        f"repos/{R}/issues/87": {"number": 87, "title": "Section 4 edits", "html_url": f"https://github.com/{R}/pull/87",
                                 "state": "open", "comments": 0, "labels": [], "assignees": [],
                                 "user": {"login": "tireddy2"}, "created_at": "2026-11-20T09:00:00Z", "body": "",
                                 "pull_request": {"url": f"https://api.github.com/repos/{R}/pulls/87"}},
        f"repos/{R}/issues/87/timeline?page=1&per_page=100": [],
        f"repos/{R}/pulls/87": {"merged": False, "draft": False, "mergeable_state": "clean", "head": {"sha": "abc"}},
        f"repos/{R}/pulls/87/reviews?page=1&per_page=100": reviews,
        f"repos/{R}/pulls/87/comments?page=1&per_page=100": inline,
    })
    return core.fingerprint(R, 87, ME, RULES)


MY_REVIEW = [review(6080368938, ME, "2026-11-26T20:52:15Z")]
THEIR_REVIEWS = [review(6086853996, "tireddy2", "2026-11-27T10:38:28Z"), review(6086864264, "tireddy2", "2026-11-27T10:39:25Z")]


def test_replies_on_your_review_threads_name_you_and_alert(fake):
    old = pr87(fake, MY_ROOTS, MY_REVIEW)
    new = pr87(fake, MY_ROOTS + REPLIES, MY_REVIEW + THEIR_REVIEWS)
    assert new["mentions_me"] == 2 and new["mention_ids"] == ["r4718337521", "r4718345517"]
    assert new["mention_at"] == "2026-11-27T10:39:24Z" > new["my_at"], "the replies came after your review"
    assert ("mentions", "2 replies to your review comments", None) in core.changes(old, new, ME, RULES)
    upgraded = {k: v for k, v in old.items() if k != "replies_known"}  # state saved by 0.4.8
    assert not [c for c in core.changes(upgraded, new, ME, RULES) if c[0] == "mentions"], \
        "the first reading of the threads seeds; the inbox still shows what waits on you"


def test_the_inbox_shows_the_replies_until_you_answer(fake, tmp_path, capsys):
    path = tmp_path / "s.json"
    for reviews, inline, waiting in [(MY_REVIEW + THEIR_REVIEWS, MY_ROOTS + REPLIES, True),
                                     (MY_REVIEW + THEIR_REVIEWS + [review(6088530271, ME, "2026-11-27T13:00:47Z")],
                                      MY_ROOTS + REPLIES + MY_ANSWERS, False)]:
        st = state.empty()
        st.update(items={KEY: pr87(fake, inline, reviews)}, seeded=True, login=ME)
        state.save(str(path), st)
        assert cli.main(["inbox", "--state", str(path), "--repos", R]) == 0
        you = capsys.readouterr().out.split("waiting on them")[0]
        assert (f"{KEY}" in you and "unanswered mention" in you) is waiting


def test_only_later_replies_in_your_threads_count():
    inline = [{"id": 1, "user": {"login": "maint"}, "in_reply_to_id": None, "created_at": "2026-11-01T00:00:00Z"},
              {"id": 2, "user": {"login": ME}, "in_reply_to_id": 1, "created_at": "2026-11-02T00:00:00Z"},
              {"id": 3, "user": {"login": "maint"}, "in_reply_to_id": 1, "created_at": "2026-11-03T00:00:00Z"},
              {"id": 4, "user": {"login": "dependabot[bot]"}, "in_reply_to_id": 1, "created_at": "2026-11-04T00:00:00Z"},
              {"id": 5, "user": {"login": "maint"}, "in_reply_to_id": None, "created_at": "2026-11-05T00:00:00Z"},
              {"id": 6, "user": {"login": "other"}, "in_reply_to_id": 5, "created_at": "2026-11-06T00:00:00Z"}]
    assert [c["id"] for c in core.thread_replies(inline, ME)] == [3], \
        "not the root you answered, not a bot, not a thread you never wrote in"


def test_a_pr_you_neither_wrote_nor_reviewed_costs_no_inline_call(fake):
    fp = pr87(fake, MY_ROOTS + REPLIES, THEIR_REVIEWS)
    assert fp["mentions_me"] == 0
    assert not any("pulls/87/comments" in " ".join(c) for c in fake.calls)


def test_a_roll_call_is_not_an_ask():
    crew = " ".join(f"@member{i}" for i in range(11))
    assert not core.addressed(f"<!-- weekly-outreach -->\n@{ME} {crew}\nThe weekly list is out.", ME)
    assert core.addressed(f"@{ME} @maint @other could you look?", ME)
    assert not core.addressed("@octocat-bot hi", ME)


def test_a_roll_call_comment_leaves_no_unanswered_mention(fake):
    crew = " ".join(f"@member{i}" for i in range(11))
    S = "vllm-project/semantic-router"
    fake.responses.update({
        f"repos/{S}/issues/2967": {"number": 2967, "title": "[WG] Data Plane", "html_url": f"https://github.com/{S}/issues/2967",
                                   "state": "open", "comments": 1, "labels": [], "assignees": [], "user": {"login": "maint"},
                                   "created_at": "2026-08-24T00:00:00Z", "body": ""},
        f"repos/{S}/issues/2967/comments?page=1&per_page=100": [
            {"id": 9, "user": {"login": "maint"}, "author_association": "MEMBER", "created_at": "2026-11-02T07:15:04Z",
             "body": f"<!-- workgroup-operations:weekly-outreach:data-plane-networking:2026-11-02 -->\n@{ME} {crew}\n"}],
        f"repos/{S}/issues/2967/timeline?page=1&per_page=100": [],
    })
    fp = core.fingerprint(S, 2967, ME, packs.for_repo(packs.load([]), S))
    assert (fp["mentions_me"], fp["mention_ids"], fp["mention_at"]) == (0, [], None)


def test_hidden_logins_do_not_make_a_real_ask_a_roll_call(fake):
    hidden = " ".join(f"@member{i}" for i in range(9))
    body = f"<!-- cc {hidden} -->\n> @old said this\n@{ME} can you rerun on the GH200?"
    assert core.addressed(core.visible(body), ME)
    S = "vllm-project/semantic-router"
    fake.responses.update({
        f"repos/{S}/issues/5": {"number": 5, "title": "t", "html_url": f"https://github.com/{S}/issues/5", "state": "open",
                                "comments": 1, "labels": [], "assignees": [], "user": {"login": "maint"},
                                "created_at": "2026-11-01T00:00:00Z", "body": ""},
        f"repos/{S}/issues/5/comments?page=1&per_page=100": [
            {"id": 7, "user": {"login": "maint"}, "author_association": "MEMBER", "created_at": "2026-11-02T00:00:00Z", "body": body}],
        f"repos/{S}/issues/5/timeline?page=1&per_page=100": [],
    })
    assert core.fingerprint(S, 5, ME, packs.for_repo(packs.load([]), S))["mention_ids"] == [7]


def test_a_roll_call_reply_on_your_thread_is_not_a_reply_to_you():
    crew = " ".join(f"@member{i}" for i in range(10))
    inline = [MY_ROOTS[0], dict(REPLIES[0], body=f"{crew} weekly sync is out")]
    assert core.thread_replies(inline, ME) == []


def test_a_quiet_title_upgrade_does_not_alert_old_replies(fake):
    import re
    quiet = dict(RULES, quiet_titles=[re.compile("Section")])
    old = pr87(fake, MY_ROOTS, MY_REVIEW)
    new = pr87(fake, MY_ROOTS + REPLIES, MY_REVIEW + THEIR_REVIEWS)
    upgraded = {k: v for k, v in old.items() if k != "replies_known"}
    assert not [c for c in core.changes(upgraded, new, ME, quiet) if c[0] == "mentions"]
    assert ("mentions", "2 comments naming you", None) in core.changes(old, new, ME, quiet)


def test_a_first_review_and_a_reply_between_polls_alerts(fake):
    """The PR was watched before you reviewed it: that reading counted no replies, rightly, and still
    marks them known, so a reply that lands before the next poll alerts."""
    old = pr87(fake, [], THEIR_REVIEWS[:0])
    assert old["replies_known"] and not any("pulls/87/comments" in " ".join(c) for c in fake.calls)
    new = pr87(fake, MY_ROOTS + REPLIES, MY_REVIEW + THEIR_REVIEWS)
    assert ("mentions", "2 replies to your review comments", None) in core.changes(old, new, ME, RULES)
