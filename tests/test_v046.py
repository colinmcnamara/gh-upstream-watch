"""0.4.6, from a day of live alerts on semantic-router#4658 and #4555 (2026-10-09 to 10-10): GitHub
keeps a thread's notification reason for every later update, so 21 "someone mentioned you" came
from CI runs, labels, approvals and the merge, with one real mention among them. A mention or an
assignment now alerts only when it is new; an issue closed as completed, and a label rule on a
closed item, are no longer asks."""
from gh_upstream_watch import core, packs, state

ME = "octocat"
R = "acme/widgets"
NOTES = "notifications?all=true&page=1&participating=true&per_page=50&since=SINCE"
PREV = "2026-10-09T18:21:15Z"  # your own last comment; the next updates were CI, labels and approvals
PR = f"https://api.github.com/repos/{R}/pulls/7"


def note(reason="mention", kind="pulls", latest=None):
    url = f"https://api.github.com/repos/{R}/{kind}/7"
    return {"id": "n1", "reason": reason, "updated_at": "2026-10-10T15:37:50Z", "repository": {"full_name": R},
            "subject": {"title": "Fix", "url": url, "latest_comment_url": latest or url}}


def thread(fake, comments=(), reviews=(), inline=(), body="Fixes the order", author=ME, created="2026-10-02T00:00:00Z"):
    fake.responses.update({
        f"repos/{R}/issues/7/comments?per_page=100&since=SINCE": list(comments),
        f"repos/{R}/pulls/7/reviews?page=1&per_page=100": list(reviews),
        f"repos/{R}/pulls/7/comments?per_page=100&since=SINCE": list(inline),
        f"repos/{R}/issues/7": {"user": {"login": author}, "body": body, "created_at": created,
                                "html_url": f"https://github.com/{R}/pull/7"},
    })


def asks(fake, n, prev=PREV, record=None):
    fake.responses[NOTES] = [n]
    return core.notification_asks({"n1": prev}, 30, 1.79e9, set(), ("*",), ME, record=record)


def review(login, verdict, at, body="", rid=1):
    return {"id": rid, "user": {"login": login}, "author_association": "MEMBER", "state": verdict, "submitted_at": at, "body": body,
            "html_url": f"https://github.com/{R}/pull/7#pullrequestreview-1"}


def test_a_pr_update_that_names_nobody_does_not_alert(fake):
    """#4658 on 2026-10-10: approvals, a label and the merge, with GitHub pointing at the PR itself."""
    thread(fake, reviews=[review("Xunzhuo", "APPROVED", "2026-10-10T10:31:10Z"),
                          review("wilsonwu", "APPROVED", "2026-10-10T11:06:55Z")])
    assert asks(fake, note()) == []


def test_an_inline_review_comment_naming_you_alerts_with_who(fake):
    """#4658 on 2026-10-08: 1fanwang's inline reply was the one real mention."""
    thread(fake, inline=[{"user": {"login": "1fanwang"}, "author_association": "COLLABORATOR",
                          "body": "@octocat option 2, please", "created_at": "2026-10-09T23:33:48Z",
                          "updated_at": "2026-10-09T23:33:48Z", "html_url": f"https://github.com/{R}/pull/7#discussion_r1"}])
    got = asks(fake, note())
    assert got[0]["message"] == '@1fanwang mentioned you: "@octocat option 2, please"'
    assert got[0]["url"].endswith("#discussion_r1")


def test_a_review_body_naming_you_alerts_once(fake):
    thread(fake, reviews=[review("maint", "COMMENTED", "2026-10-10T01:00:00Z", "@octocat can you split this?")])
    assert asks(fake, note())[0]["message"] == '@maint mentioned you: "@octocat can you split this?"'
    assert asks(fake, note(), prev="2026-10-10T01:00:00Z") == [], "a review already handled is not found again"


def test_a_review_list_that_could_not_be_read_still_alerts(fake):
    thread(fake, comments=[{"user": {"login": "x"}, "body": "+1", "created_at": "2026-10-10T00:00:00Z"}])
    fake.responses[f"repos/{R}/pulls/7/reviews?page=1&per_page=100"] = {"__error__": "gh: Server Error (HTTP 502)"}
    assert [a["message"] for a in asks(fake, note())] == ["someone mentioned you"]


def test_an_old_body_does_not_count_because_github_points_at_the_item(fake):
    """GitHub sets latest_comment_url to the item for a close or a label, not only for a body edit."""
    thread(fake, body="cc @octocat", author="lead")
    assert asks(fake, note(kind="issues")) == []
    assert asks(fake, note(kind="issues"), prev="2026-10-01T00:00:00Z")[0]["message"].startswith("@lead mentioned you"), \
        "an item new since the last handled update still counts"


def test_a_review_edited_to_name_you_is_news_though_its_time_is_old(fake):
    """Codex on 0.4.6: an edit keeps a review's submitted_at, so the thread remembers what named you."""
    thread(fake, reviews=[review("maint", "COMMENTED", "2026-10-08T12:00:00Z", "@octocat please rebase", rid=5)])
    record = {"n1": []}  # the last look found nothing naming you
    assert asks(fake, note(), record=record)[0]["message"] == '@maint mentioned you: "@octocat please rebase"'
    assert record == {"n1": ["review:5"]}
    assert asks(fake, note(), record=record) == [], "named once, alerted once"


def test_a_body_edited_to_name_you_is_news_though_the_item_is_old(fake):
    thread(fake, body="cc @octocat", author="lead")
    record = {"n1": []}
    assert asks(fake, note(kind="issues"), record=record)[0]["message"].startswith("@lead mentioned you")
    assert asks(fake, note(kind="issues"), record=record) == [] and record == {"n1": ["body"]}


def test_a_thread_without_a_record_goes_by_time_and_starts_one(fake):
    """The first look after the upgrade: an old review naming you is history, not news."""
    thread(fake, reviews=[review("maint", "COMMENTED", "2026-10-08T12:00:00Z", "@octocat please rebase", rid=5)])
    record = {}
    assert asks(fake, note(), record=record) == [] and record == {"n1": ["review:5"]}
    st = {"notifications": {"seen": {}, "named": record}, "claimable": {"seen": {}}, "approvals": {"seen": {}}, "slack": {}}
    state.prune(st, 1.79e9, 30)
    assert st["notifications"]["named"] == {}, "a thread forgotten from seen is forgotten here too"


def events(fake, *assigned_at, error=False):
    key = f"repos/{R}/issues/7/events?page=1&per_page=100"
    fake.responses[key] = ({"__error__": "gh: Server Error (HTTP 502)"} if error else
                           [{"event": "assigned", "assignee": {"login": ME}, "created_at": at} for at in assigned_at])


def test_an_update_after_your_assignment_does_not_assign_you_again(fake):
    """#4555 on 2026-10-10: closed by the merge, it said "someone assigned you" four days late."""
    events(fake, "2026-10-06T12:45:00Z")
    assert asks(fake, note("assign", "issues")) == []
    events(fake, "2026-10-06T12:45:00Z", "2026-10-10T15:00:00Z")
    assert [a["message"] for a in asks(fake, note("assign", "issues"))] == ["someone assigned you"]
    events(fake, error=True)
    assert [a["message"] for a in asks(fake, note("assign", "issues"))] == ["someone assigned you"], "unknown alerts"


def issue_fp(fake, rules, state="open", closed_by=None, state_reason=None, labels=()):
    fake.responses.update({
        f"repos/{R}/issues/5": {"number": 5, "title": "Bug", "html_url": f"https://github.com/{R}/issues/5", "state": state,
                                "comments": 0, "labels": [{"name": x} for x in labels], "assignees": [{"login": ME}],
                                "user": {"login": ME}, "created_at": "2026-10-01T00:00:00Z", "body": "",
                                "closed_by": {"login": closed_by} if closed_by else None, "state_reason": state_reason},
        f"repos/{R}/issues/5/timeline?page=1&per_page=100": [],
    })
    return core.fingerprint(R, 5, ME, rules)


def test_your_issue_closed_as_completed_is_news_not_an_ask(fake):
    """#4555: mergify closed it as completed when it merged the PR that fixes it."""
    rules = packs.for_repo(packs.load([]), R)
    old = issue_fp(fake, rules)
    done = issue_fp(fake, rules, "closed", "mergify[bot]", "completed")
    assert [c[0] for c in core.changes(old, done, ME, rules)] == ["state"]
    dropped = issue_fp(fake, rules, "closed", "maint", "not_planned")
    assert ("closed", "CLOSED by @maint without merging: check why", None) in core.changes(old, dropped, ME, rules)


def test_a_label_rule_does_not_fire_on_a_closed_item(fake):
    """#4555: the bot removed in-progress when the issue closed; that is not a label to ask for."""
    rules = packs.for_repo(packs.load([]), "vllm-project/semantic-router")
    old = issue_fp(fake, rules, labels=("accepted", "in-progress"))
    closed = issue_fp(fake, rules, "closed", "mergify[bot]", "completed", labels=("accepted",))
    assert not [c for c in core.changes(old, closed, ME, rules) if c[0] == "label_rule"]
    reopened = issue_fp(fake, rules, labels=("accepted",))
    assert [c for c in core.changes(old, reopened, ME, rules) if c[0] == "label_rule"], "an open item still asks"


# --- review of 0.4.6 (Opus) ----------------------------------------------------------------------

def test_a_partial_look_never_starts_a_record(fake):
    """Opus 1: a comment naming you ends the look before the body is read; an empty record would make
    the old body "new" on the next CI update."""
    thread(fake, body="cc @octocat", author="lead", created="2026-10-01T00:00:00Z",
           comments=[{"user": {"login": "maint"}, "body": "@octocat ping", "created_at": "2026-10-10T00:00:00Z"}])
    record = {}
    assert asks(fake, note(), record=record)[0]["message"].startswith("@maint mentioned you") and record == {}
    thread(fake, body="cc @octocat", author="lead", created="2026-10-01T00:00:00Z")
    assert asks(fake, note(), prev="2026-10-10T00:00:00Z", record=record) == []
    fake.responses[f"repos/{R}/pulls/7/reviews?page=1&per_page=100"] = {"__error__": "gh: Server Error (HTTP 502)"}
    fake.responses[f"repos/{R}/issues/7/comments?per_page=100&since=SINCE"] = [{"user": {"login": "x"}, "body": "+1"}]
    assert asks(fake, note(), record=(blind := {})) and blind == {}, "a blind look alerts and records nothing"


def test_a_new_items_body_alerts_once(fake):
    """Opus 2: GitHub's first update of a new item has its creation time; the next one is not news."""
    thread(fake, body="cc @octocat", author="lead", created=PREV)
    assert asks(fake, note(kind="issues")) == []


def test_a_pr_with_over_100_reviews_is_read_whole_not_unknown(fake):
    """Opus 3: every lone inline reply is a review; a busy PR passes 100 and must not alert every update."""
    thread(fake)
    fake.responses[f"repos/{R}/pulls/7/reviews?page=1&per_page=100"] = [
        review("maint", "COMMENTED", "2026-10-01T00:00:00Z", rid=i) for i in range(100)]
    fake.responses[f"repos/{R}/pulls/7/reviews?page=2&per_page=100"] = [
        review("maint", "COMMENTED", "2026-10-10T00:00:00Z", "@octocat last one", rid=100)]
    assert asks(fake, note())[0]["message"] == '@maint mentioned you: "@octocat last one"'


def test_a_reply_that_is_not_a_list_is_unknown_not_a_crash(fake):
    """Opus 7: an error object where a list belongs fails the lookup, not the notifications source."""
    thread(fake)
    fake.responses[f"repos/{R}/issues/7/comments?per_page=100&since=SINCE"] = {"message": "Moved Permanently"}
    assert [a["message"] for a in asks(fake, note())] == ["someone mentioned you"]


def test_your_pr_closed_unmerged_still_needs_you_whatever_its_reason(fake):
    """Opus 5: only an issue closed as completed is plain news."""
    rules = packs.for_repo(packs.load([]), R)
    old = issue_fp(fake, rules)
    new = dict(issue_fp(fake, rules, "closed", "maint", "completed"), pr=True)
    assert [c[0] for c in core.changes(dict(old, pr=True), new, ME, rules)] == ["closed"]
