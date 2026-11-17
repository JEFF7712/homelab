import subprocess
import unittest

from scripts.deploy_fleet import (
    CRASH_LOOP_INTERVAL,
    CRASH_LOOP_MAX_WINDOW,
    CRASH_LOOP_PROBE,
    CRASH_LOOP_WINDOW,
    FLEET_HOSTS,
    FleetDeployer,
    FleetDeploymentError,
    UnitSample,
    classify_crash_loops,
    looping_before_switch,
    parse_unit_samples,
)

# Verbatim shape of `systemctl show` on homelab-04 (2026-11-17): systemd
# prints properties in its own order, not the requested one.
SHOW_OUTPUT = """Id=acpid.service
ActiveState=active
SubState=running
ActiveEnterTimestamp=Fri 2026-11-13 03:19:05 CST
NRestarts=0

Id=lastlog2-import.service
ActiveState=active
SubState=exited
ActiveEnterTimestamp=Fri 2026-11-13 03:19:06 CST
NRestarts=0

Id=monero.service
ActiveState=activating
SubState=auto-restart
ActiveEnterTimestamp=Mon 2026-11-16 14:44:47 CST
NRestarts=7

Id=dev-ttyS0.device
ActiveState=active
SubState=plugged
"""


def unit(restarts, state="active", sub="running", entered="t0"):
    return UnitSample(restarts, state, sub, entered)


def series(*states):
    """Samples of one unit, monero.service, at consecutive intervals."""
    return [{"monero.service": state} for state in states]


class ParseUnitSamplesTest(unittest.TestCase):
    def test_parses_blocks_in_systemd_property_order(self):
        samples = parse_unit_samples(SHOW_OUTPUT)
        self.assertEqual(
            samples["monero.service"],
            UnitSample(7, "activating", "auto-restart", "Mon 2026-11-16 14:44:47 CST"),
        )
        self.assertEqual(samples["lastlog2-import.service"].sub_state, "exited")

    def test_skips_blocks_without_restart_counter(self):
        samples = parse_unit_samples(SHOW_OUTPUT + "\nId=x.service\nNRestarts=\n")
        self.assertNotIn("dev-ttyS0.device", samples)
        self.assertNotIn("x.service", samples)
        self.assertEqual(parse_unit_samples(""), {})


class ClassifyCrashLoopsTest(unittest.TestCase):
    def test_unit_restarting_every_few_seconds_is_looping(self):
        looping, unsettled = classify_crash_loops(
            series(
                unit(3, "activating", "auto-restart", "a"),
                unit(5, "active", "running", "b"),
                unit(7, "activating", "auto-restart", "b"),
            )
        )
        self.assertEqual(looping, {"monero.service": 4})
        self.assertEqual(unsettled, set())

    def test_slow_loop_in_restart_delay_is_unsettled(self):
        # RestartSec=30s: one restart in the window, then back in its delay.
        _, unsettled = classify_crash_loops(
            series(
                unit(1, "activating", "auto-restart", "a"),
                unit(2, "active", "running", "b"),
                unit(2, "activating", "auto-restart", "b"),
                unit(2, "activating", "auto-restart", "b"),
            )
        )
        self.assertEqual(unsettled, {"monero.service"})

    def test_unit_restarted_once_at_switch_and_now_running_is_not_flagged(self):
        # Its switch-time restart is in NRestarts before the first sample.
        steady = unit(1, "active", "running", "switch")
        self.assertEqual(
            classify_crash_loops(series(steady, steady, steady, steady, steady)),
            ({}, set()),
        )

    def test_one_restart_inside_window_then_steady_is_not_flagged(self):
        self.assertEqual(
            classify_crash_loops(
                series(
                    unit(0, "activating", "auto-restart", "a"),
                    unit(1, "active", "running", "b"),
                    unit(1, "active", "running", "b"),
                )
            ),
            ({}, set()),
        )

    def test_oneshot_that_runs_and_exits_is_not_flagged(self):
        # A timer-started oneshot: activations do not count as restarts.
        self.assertEqual(
            classify_crash_loops(
                series(
                    unit(0, "inactive", "dead", ""),
                    unit(0, "activating", "start", ""),
                    unit(0, "inactive", "dead", "t1"),
                    unit(0, "active", "exited", "t2"),
                )
            ),
            ({}, set()),
        )

    def test_restarts_before_the_window_are_not_flagged(self):
        steady = unit(250, "active", "running", "long-ago")
        self.assertEqual(
            classify_crash_loops(series(steady, steady, steady)), ({}, set())
        )

    def test_counter_reset_by_manual_start_counts_only_new_restarts(self):
        looping, _ = classify_crash_loops(
            series(
                unit(9, "active", "running", "a"),
                unit(0, "active", "running", "b"),
                unit(2, "activating", "auto-restart", "c"),
            )
        )
        self.assertEqual(looping, {"monero.service": 2})

    def test_exempt_units_are_ignored(self):
        samples = series(unit(1), unit(5, "activating", "auto-restart"))
        self.assertEqual(
            classify_crash_loops(samples, exempt={"monero.service"}), ({}, set())
        )

    def test_units_looping_before_switch(self):
        before = {"a.service": unit(3), "b.service": unit(0), "c.service": unit(0)}
        after = {
            "a.service": unit(4),
            "b.service": unit(0),
            "c.service": unit(0, "activating", "auto-restart"),
        }
        self.assertEqual(
            looping_before_switch(before, after), {"a.service", "c.service"}
        )


class VerifyNoCrashLoopsTest(unittest.TestCase):
    def deployer(self, outputs):
        self.sleeps = []
        replies = iter(outputs)

        def runner(argv, **kwargs):
            self.assertEqual(argv[-1], CRASH_LOOP_PROBE)
            return subprocess.CompletedProcess(argv, 0, next(replies), "")

        return FleetDeployer(
            targets=[FLEET_HOSTS["homelab-04"]],
            runner=runner,
            sleeper=self.sleeps.append,
            enable_notifications=False,
        )

    @staticmethod
    def show(restarts, state="active\nSubState=running", entered="x"):
        return (
            f"Id=monero.service\nActiveState={state}\nNRestarts={restarts}\n"
            f"ActiveEnterTimestamp={entered}\n"
        )

    def test_healthy_host_takes_the_window_only(self):
        deployer = self.deployer([self.show(1)] * 10)
        with self.assertLogs("deploy_fleet", "INFO") as logs:
            deployer.verify_no_crash_loops(FLEET_HOSTS["homelab-04"])
        self.assertEqual(sum(self.sleeps), CRASH_LOOP_WINDOW)
        self.assertIn("Crash-loop check passed", "\n".join(logs.output))

    def test_slow_loop_is_flagged_after_the_extended_window(self):
        delay = "activating\nSubState=auto-restart"
        outputs = [self.show(0, delay)] + [self.show(1, delay)] * 20
        deployer = self.deployer(outputs)
        with self.assertRaisesRegex(
            FleetDeploymentError, r"crash-looping.*monero\.service \(1 restart\)"
        ):
            deployer.verify_no_crash_loops(FLEET_HOSTS["homelab-04"])
        self.assertEqual(sum(self.sleeps), CRASH_LOOP_MAX_WINDOW)

    def test_unsettled_unit_that_recovers_passes_before_the_limit(self):
        delay = "activating\nSubState=auto-restart"
        outputs = [self.show(0, delay)] * 4 + [self.show(1, entered="y")] * 20
        deployer = self.deployer(outputs)
        deployer.verify_no_crash_loops(FLEET_HOSTS["homelab-04"])
        self.assertLess(sum(self.sleeps), CRASH_LOOP_MAX_WINDOW)
        self.assertEqual(set(self.sleeps), {CRASH_LOOP_INTERVAL})


if __name__ == "__main__":
    unittest.main()
