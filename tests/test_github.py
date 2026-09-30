"""The GitHub door: GET only, full pagination, and 'unknown' instead of 'empty' on any failure."""
import sys

import fake_gh
import pytest

from gh_upstream_watch import github


def test_every_call_is_a_get(fake):
    fake.responses = {"user": {"login": "octocat"}}
    github.gh_get("user")
    assert fake.calls[0][1:5] == ["api", "--method", "GET", "user"]


@pytest.mark.parametrize("args", [
    ["api", "user"], ["api", "--method", "POST", "repos/acme/widgets/issues"],
    ["api", "--method", "GET", "-X", "PATCH", "x"], ["api", "--method", "GET", "x", "--input", "f"], ["pr", "list"]])
def test_fake_gh_refuses_anything_else(args):
    code, _, err = fake_gh.respond({"responses": {}}, args)
    assert code == 2 and "fake gh" in err


def test_paginate_reads_every_page(fake):
    fake.responses = {"x?page=1&per_page=2": [1, 2], "x?page=2&per_page=2": [3, 4], "x?page=3&per_page=2": [5]}
    assert github.paginate("x", per_page=2) == [1, 2, 3, 4, 5]


def test_a_failed_page_fails_the_list(fake):
    fake.responses = {"x?page=1&per_page=2": [1, 2], "x?page=2&per_page=2": {"__error__": "HTTP 502"}}
    with pytest.raises(github.GHError):
        github.paginate("x", per_page=2)


def test_page_cap_is_incomplete_not_truncated(fake, monkeypatch):
    monkeypatch.setattr(github, "MAX_PAGES", 2)
    fake.responses = {"x?page=1&per_page=1": [1], "x?page=2&per_page=1": [2]}
    with pytest.raises(github.Incomplete):
        github.paginate("x", per_page=1)


def search_page(page, items, total, incomplete=False):
    return {f"search/issues?page={page}&per_page=2&q=repo:acme/widgets": {
        "total_count": total, "incomplete_results": incomplete, "items": items}}


def test_search_paginates(fake):
    fake.responses = {**search_page(1, [{"number": 1}, {"number": 2}], 3), **search_page(2, [{"number": 3}], 3)}
    assert [i["number"] for i in github.search_issues("repo:acme/widgets", per_page=2)] == [1, 2, 3]


def test_search_incomplete_results_is_unknown(fake):
    fake.responses = search_page(1, [{"number": 1}], 1, incomplete=True)
    with pytest.raises(github.Incomplete):
        github.search_issues("repo:acme/widgets", per_page=2)


def test_search_over_the_cap_is_unknown(fake):
    fake.responses = search_page(1, [{"number": 1}, {"number": 2}], 1500)
    with pytest.raises(github.Incomplete, match="cap"):
        github.search_issues("repo:acme/widgets", per_page=2)


def test_bad_json_is_an_error(monkeypatch):
    monkeypatch.setattr(github, "_run", lambda argv: "<html>rate limited</html>")
    with pytest.raises(github.GHError):
        github.gh_get("user")


def test_real_subprocess_failures_are_gh_errors(monkeypatch, tmp_path):
    monkeypatch.setenv("GH_UPSTREAM_WATCH_GH", str(tmp_path / "no-such-gh"))
    with pytest.raises(github.GHError):
        github.gh_get("user")
    fail = tmp_path / "gh"
    fail.write_text(f"#!{sys.executable}\nimport sys; sys.stderr.write('HTTP 401'); sys.exit(1)\n")
    fail.chmod(0o755)
    monkeypatch.setenv("GH_UPSTREAM_WATCH_GH", str(fail))
    with pytest.raises(github.GHError, match="401"):
        github.gh_get("user")


def test_html_url():
    assert github.html_url("https://api.github.com/repos/acme/widgets/pulls/9") == "https://github.com/acme/widgets/pull/9"
