"""0.4.5, from a read-only audit of the watcher against live threads (Switchyard#855, semantic-router#4658):
a maintainer who spoke after you puts the item on you, review bodies and inline review comments are read
for mentions, and notifications you already read on another device still alert."""
import time

from gh_upstream_watch import cli, core, packs

ME = "octocat"
R = "acme/widgets"
RULES = packs.for_repo(packs.load([]), R)
NOTES = "notifications?all=true&page=1&participating=true&per_page=50&since=SINCE"


def pr(fake, n=7, author=ME, comments=(), reviews=(), inline=None, state="open"):
    fake.responses.update({
        f"repos/{R}/issues/{n}": {"number": n, "title": "Fix", "html_url": f"https://github.com/{R}/pull/{n}",
                                  "state": state, "comments": len(comments), "labels": [], "assignees": [],
                                  "user": {"login": author}, "created_at": "2026-09-26T00:00:00Z", "body": "",
                                  "pull_request": {}},
        f"repos/{R}/issues/{n}/comments?page=1&per_page=100": list(comments),
        f"repos/{R}/issues/{n}/timeline?page=1&per_page=100": [],
        f"repos/{R}/pulls/{n}": {"merged": False, "mergeable_state": "clean"},
        f"repos/{R}/pulls/{n}/reviews?page=1&per_page=100": list(reviews),
    })
    if inline is not None:
        fake.responses[f"repos/{R}/pulls/{n}/comments?page=1&per_page=100"] = list(inline)


def comment(i, login, assoc, at, body="a question"):
    return {"id": i, "user": {"login": login}, "author_association": assoc, "body": body, "created_at": at}


def review(i, login, assoc, state, at, body=""):
    return {"id": i, "user": {"login": login}, "author_association": assoc, "state": state, "submitted_at": at, "body": body}


def reasons(fp):
    return cli._you_reasons(fp, ME, {})


MERGERS = {R: {"afourniernv": {"merges": True, "at": time.time()}}}


def test_a_maintainer_question_after_your_last_word_waits_on_you(fake):
    """Switchyard#855 on 2026-09-27: your last word was a review-thread reply at 13:19; afourniernv
    asked a plain question (no @) at 16:39."""
    pr(fake, reviews=[review(1, ME, "NONE", "COMMENTED", "2026-09-27T13:19:52Z")],
       comments=[comment(2, "afourniernv", "CONTRIBUTOR", "2026-09-27T16:39:39Z")], inline=[])
    fp = core.fingerprint(R, 7, ME, RULES, mergers=MERGERS)
    assert reasons(fp) == ["maintainer replied after you"]
    pr(fake, reviews=[review(1, ME, "NONE", "COMMENTED", "2026-09-27T13:19:52Z")],
       comments=[comment(2, "afourniernv", "CONTRIBUTOR", "2026-09-27T16:39:39Z"),
                 comment(3, ME, "NONE", "2026-09-27T20:04:55Z", "Not live, no.")], inline=[])
    assert reasons(core.fingerprint(R, 7, ME, RULES, mergers=MERGERS)) == [], "you answered"


def test_an_approval_after_your_last_word_does_not(fake):
    pr(fake, comments=[comment(1, ME, "NONE", "2026-09-27T10:00:00Z")],
       reviews=[review(2, "maint", "COLLABORATOR", "APPROVED", "2026-09-28T00:00:00Z")], inline=[])
    fp = core.fingerprint(R, 7, ME, RULES)
    assert fp["their_at"] == "2026-09-28T00:00:00Z" and reasons(fp) == []


def test_only_your_own_items_wait_on_you_for_a_maintainer_reply(fake):
    pr(fake, author="someone", comments=[comment(1, ME, "NONE", "2026-09-27T10:00:00Z"),
                                         comment(2, "maint", "COLLABORATOR", "2026-09-28T00:00:00Z")])
    assert reasons(core.fingerprint(R, 7, ME, RULES)) == []


def test_an_inline_review_comment_naming_you_is_an_unanswered_mention(fake):
    """semantic-router#4658: 1fanwang's 16:11 inline reply "@you option 2, please" came after your 13:57 reply."""
    pr(fake, reviews=[review(1, "Xunzhuo", "MEMBER", "CHANGES_REQUESTED", "2026-10-08T09:00:36Z", "Thanks for the regression."),
                      review(2, ME, "CONTRIBUTOR", "COMMENTED", "2026-10-08T13:57:02Z"),
                      review(3, "1fanwang", "COLLABORATOR", "COMMENTED", "2026-10-08T16:11:25Z")],
       inline=[comment(10, ME, "CONTRIBUTOR", "2026-10-08T13:57:02Z", "Thanks, the short-think delta is real"),
               comment(11, "1fanwang", "COLLABORATOR", "2026-10-08T16:11:25Z", "@octocat option 2, please")])
    fp = core.fingerprint(R, 7, ME, RULES)
    assert fp["mention_at"] == "2026-10-08T16:11:25Z"
    assert reasons(fp) == ["unanswered mention"]


def test_a_review_body_naming_you_is_a_mention(fake):
    pr(fake, comments=[comment(1, ME, "NONE", "2026-10-01T00:00:00Z")],
       reviews=[review(2, "maint", "COLLABORATOR", "COMMENTED", "2026-10-02T00:00:00Z", "@octocat can you split this?")],
       inline=[])
    assert core.fingerprint(R, 7, ME, RULES)["mention_at"] == "2026-10-02T00:00:00Z"


def test_inline_comments_are_read_only_on_your_own_open_prs_with_a_human_review(fake):
    pr(fake, author="someone", reviews=[review(1, "maint", "COLLABORATOR", "COMMENTED", "2026-10-02T00:00:00Z")])
    core.fingerprint(R, 7, ME, RULES)
    pr(fake, n=8, reviews=[review(1, "coderabbitai[bot]", "CONTRIBUTOR", "COMMENTED", "2026-10-02T00:00:00Z")])
    core.fingerprint(R, 8, ME, RULES)
    assert not [argv for argv in fake.calls if any("/comments" in a and "/pulls/" in a for a in argv)]


def note(nid, updated, unread):
    return {"id": nid, "reason": "mention", "updated_at": updated, "unread": unread, "repository": {"full_name": R},
            "subject": {"title": "Fix x", "url": f"https://api.github.com/repos/{R}/issues/5"}}


def test_a_thread_you_read_elsewhere_still_alerts_after_the_first_run(fake):
    fake.responses[NOTES] = [note("1", "t1", unread=False), note("2", "t1", unread=True), note("3", "t2", unread=False)]
    seen = {"3": "t1"}  # updated and read on another device while only unread threads were polled
    first = core.notification_asks(seen, 30, 1.7e9, set(), seed_read=True)
    assert [a["message"] for a in first] == ["someone mentioned you"], "the read backlog is recorded, not alerted"
    assert seen == {"1": "t1", "2": "t1", "3": "t2"}
    fake.responses[NOTES][0]["updated_at"] = "t2"  # a new mention, read on your phone before the next run
    assert len(core.notification_asks(seen, 30, 1.7e9, set())) == 1

