"""0.3.3, from real data: a Mac that sleeps. launchd runs a calendar job missed during sleep once on
wake (StartInterval did not: 00:23, then nothing until a manual run at 02:53), and the first run after
wake met a network that was not up yet (`i/o timeout`, counted as unknown). Scheduled runs now wait
for GitHub first, and skip cleanly if it never answers."""
import json
import plistlib

from gh_upstream_watch import cli, github, state

R = "acme/widgets"


def plist(capsys, interval):
    assert cli.main(["--print-plist", "--interval", str(interval)]) == 0
    return plistlib.loads(capsys.readouterr().out.encode())


def test_launchd_uses_calendar_slots_so_a_missed_run_fires_on_wake(capsys):
    p = plist(capsys, 30)
    assert p["StartCalendarInterval"] == [{"Minute": 0}, {"Minute": 30}] and "StartInterval" not in p
    assert p["ProgramArguments"][-2:] == ["--wait-network", "180"], "scheduled runs wait for the network"
    p = plist(capsys, 45)
    assert p["StartInterval"] == 2700 and "StartCalendarInterval" not in p, "45 does not divide 60: unchanged"


def test_other_schedulers_wait_for_the_network_too(capsys):
    assert cli.main(["--print-cron"]) == 0 and "--once --wait-network 180" in capsys.readouterr().out


class Net:
    """gh that fails `rate_limit` with a network error `down` times, then answers."""

    def __init__(self, down, err='Get "https://api.github.com/rate_limit": dial tcp: i/o timeout'):
        self.down, self.err, self.probes, self.slept, self.timeouts, self.slow = down, err, 0, 0, [], False

    def run(self, argv, timeout=None):
        self.timeouts.append(timeout)
        if self.slow and timeout:
            self.slept += timeout  # a dropped-packet probe uses its whole timeout
        if argv[4] != "rate_limit":
            raise github.GHError(f"{argv[4]}: unexpected call")
        self.probes += 1
        if self.down is None or self.probes <= self.down:
            raise github.GHError(f"rate_limit: gh exited 1: {self.err}")
        return "{}"

    def sleep(self, s):
        self.slept += s


def wire(monkeypatch, net):
    monkeypatch.setattr(github, "_run", net.run)
    monkeypatch.setattr(github.time, "sleep", net.sleep)
    t = [0.0]
    monkeypatch.setattr(github.time, "monotonic", lambda: t[0] + net.slept)


def test_wait_online_rides_out_a_network_coming_up(monkeypatch):
    net = Net(down=2)
    wire(monkeypatch, net)
    assert github.wait_online(180) is True and net.probes == 3 and 0 < net.slept < 180


def test_wait_online_gives_up_after_the_limit(monkeypatch):
    net = Net(down=None)
    wire(monkeypatch, net)
    assert github.wait_online(180) is False and net.slept <= 180


def test_an_auth_error_does_not_wait(monkeypatch):
    net = Net(down=None, err="HTTP 401: Bad credentials (https://api.github.com/rate_limit)")
    wire(monkeypatch, net)
    assert github.wait_online(180) is True and net.probes == 1 and net.slept == 0, "the run reports it"


def test_offline_run_is_skipped_and_touches_nothing(tmp_path, monkeypatch, capsys):
    wire(monkeypatch, Net(down=None))
    st = state.empty()
    st.update(seeded=True, login="octocat", last_complete=1.0)
    p = tmp_path / "s.json"
    state.save(str(p), st)
    before = p.read_text()
    assert cli.main(["--once", "--wait-network", "60", "--state", str(p), "--repos", R, "--notify", "none"]) == 0
    assert "offline" in capsys.readouterr().err
    assert p.read_text() == before and not json.loads(before).get("unknown_streak")


def test_no_wait_by_default(fake, tmp_path):
    fake.load("run1")
    assert cli.main(["--state", str(tmp_path / "s.json"), "--repos", R, "--notify", "none"]) == 0
    assert not any(c[4] == "rate_limit" for c in fake.calls), "manual runs do not probe"


def test_default_is_every_15_minutes_and_stuck_still_means_three_hours(capsys):
    p = plist(capsys, 15)
    assert [d["Minute"] for d in p["StartCalendarInterval"]] == [0, 15, 30, 45]
    assert cli.main(["--print-plist"]) == 0 and "<integer>45</integer>" in capsys.readouterr().out


def test_a_certificate_error_is_not_offline(monkeypatch):
    net = Net(down=None, err='Get "https://api.github.com/rate_limit": tls: failed to verify certificate: x509')
    wire(monkeypatch, net)
    assert github.wait_online(180) is True and net.slept == 0, "it never clears: the run reports it"


def test_init_rejects_a_bad_interval(tmp_path):
    assert cli.main(["init", "--config", str(tmp_path / "c.json"), "--repos", R, "--schedule", "--interval", "0"]) == 2


def test_the_wait_stays_within_its_limit(monkeypatch):
    """Codex: each probe could take its full 60s timeout after the limit, so 180 became 240."""
    net = Net(down=None)
    net.slow = True
    wire(monkeypatch, net)
    assert github.wait_online(180) is False
    assert net.slept <= 180 and all(t <= 60 for t in net.timeouts), (net.slept, net.timeouts)


def test_gh_proxy_and_eof_failures_count_as_offline(monkeypatch):
    for err in ("gh: HTTP 503", 'Get "https://api.github.com/rate_limit": EOF'):
        net = Net(down=1, err=err)
        wire(monkeypatch, net)
        assert github.wait_online(180) is True and net.probes == 2, err


def test_a_streak_already_alerted_before_the_upgrade_does_not_alert_again():
    st = {"unknown_streak": {"notifications": {"since": 0, "runs": 20}}}
    assert cli.escalate(st, {"notifications"}, {}, 10 * 3600, dict(cli.DEFAULTS)) == []
    st = {"unknown_streak": {"notifications": {"since": 0, "runs": 3}}}
    assert len(cli.escalate(st, {"notifications"}, {}, 10 * 3600, dict(cli.DEFAULTS))) == 1, "not yet alerted: due"
