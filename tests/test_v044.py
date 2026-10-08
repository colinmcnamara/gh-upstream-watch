"""0.4.4: Xunzhuo's /accept on semantic-router#4555 is MEMBER, so the gate never fired. The repo's bot
applies `accepted` only after checking who typed /accept, so that label passes the gate. And the merge
check looks deeper: a Switchyard maintainer's own merge can sit 13th among the PRs they reviewed."""
import pytest

from gh_upstream_watch import core, packs

ME = "octocat"
SR = "vllm-project/semantic-router"
SR_RULES = packs.for_repo(packs.load([]), SR)  # the bundled pack
R = "acme/widgets"


def item(fake, repo, labels, comments, n=9, author=ME, state="open"):
    fake.responses.update({
        f"repos/{repo}/issues/{n}": {"number": n, "title": "Bug", "html_url": f"https://github.com/{repo}/issues/{n}",
                                     "state": state, "comments": len(comments), "labels": [{"name": x} for x in labels],
                                     "assignees": [], "user": {"login": author}, "created_at": "2026-10-04T19:04:49Z", "body": ""},
        f"repos/{repo}/issues/{n}/comments?page=1&per_page=100": comments,
        f"repos/{repo}/issues/{n}/timeline?page=1&per_page=100": [],
    })


def comment(i, login, assoc, body, at):
    return {"id": i, "user": {"login": login}, "author_association": assoc, "body": body, "created_at": at}


ACCEPT = comment(1, "Xunzhuo", "MEMBER", "/accept", "2026-10-06T09:00:14Z")


def test_the_bots_accepted_label_passes_the_gate_for_a_member(fake):
    item(fake, SR, ["accepted", "bug", "wg/data-plane-networking"], [ACCEPT])
    fp = core.fingerprint(SR, 9, ME, SR_RULES)
    assert fp["gates"]["accept"] == {"by": "Xunzhuo", "done": False, "bot": False, "cid": 1}
    assert ("gate", "ACCEPTED by @Xunzhuo: comment /assign now", None) in core.changes(None, fp, ME, SR_RULES)


def test_a_member_accept_without_the_label_does_not_pass(fake):
    """Any org member can type /accept; only the bot's label says it counted."""
    item(fake, SR, ["bug", "needs-acceptance"], [ACCEPT])
    assert core.fingerprint(SR, 9, ME, SR_RULES)["gates"] == {}


def test_your_assign_after_it_marks_the_gate_done(fake):
    item(fake, SR, ["accepted", "bug"], [ACCEPT, comment(2, ME, "CONTRIBUTOR", "/assign", "2026-10-06T12:30:55Z")])
    assert core.fingerprint(SR, 9, ME, SR_RULES)["gates"]["accept"]["done"] is True


def test_the_label_does_not_pass_the_gate_on_an_issue_you_did_not_file(fake):
    """#2967 (the WG charter) is Xunzhuo's own, accepted: there is nothing for you to /assign."""
    item(fake, SR, ["accepted", "community"], [ACCEPT], author="Xunzhuo")
    assert core.fingerprint(SR, 9, ME, SR_RULES)["gates"] == {}


def test_a_gate_on_a_closed_item_is_recorded_without_an_alert(fake):
    """#4118: accepted, then closed by a maintainer's rollup PR. /assign now would be wrong."""
    item(fake, SR, ["accepted", "bug"], [ACCEPT], state="closed")
    new = core.fingerprint(SR, 9, ME, SR_RULES)
    old = dict(new, gates={})
    assert new["gates"]["accept"]["by"] == "Xunzhuo"
    assert not [a for a in core.changes(old, new, ME, SR_RULES) if a[0].startswith("gate")]


def test_a_gate_label_must_be_a_string():
    gate = {"id": "accept", "comment": "^/accept", "authorized_by": {}, "alert": "x", "label": ["accepted"]}
    with pytest.raises(packs.PackError):
        packs.validate({"id": "x", "repos": ["*"], "gates": [gate]}, "test")


def test_the_merge_check_looks_past_the_first_few_reviewed_prs(fake):
    """elyasmnvidian's own merge is 13th among the merged PRs he reviewed in Switchyard."""
    item(fake, R, [], [comment(1, "lead", "CONTRIBUTOR", "a question", "2026-10-01T00:00:00Z")])
    merged_by = ["other"] * 12 + ["lead"]
    fake.responses[f"search/issues?per_page=20&q=repo:{R} is:pr is:merged reviewed-by:lead "
                   f"-author:lead&sort=updated"] = {"items": [{"number": 40 + i} for i in range(len(merged_by))]}
    for i, m in enumerate(merged_by):
        fake.responses[f"repos/{R}/pulls/{40 + i}"] = {"merged_by": {"login": m}}
    assert core.fingerprint(R, 9, ME, packs.for_repo(packs.load([]), R))["their_at"] == "2026-10-01T00:00:00Z"
