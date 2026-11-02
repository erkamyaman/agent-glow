import contextlib
import io
import json
import os
import plistlib
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import agent_glow as ag  # noqa: E402
import install  # noqa: E402

REAL_PLATFORM = install.platform_name(sys.platform)
GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
# Formatted like a real ~/.claude/settings.json: 2 spaces, no trailing newline.
CLAUDE_ORIGINAL = '{\n  "enabledPlugins": {\n    "x@y": true\n  },\n  "tui": "fullscreen"\n}'
USER_HOOK = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo audit", "timeout": 3}]}


class FakeRun:
    """Stands in for subprocess.run: records commands, emulates launchctl, systemctl, uv, venv and pip."""

    def __init__(self, hid_ok=True, bootstrap_failures=0, fail=None, devices=(0, "")):
        self.calls = []
        self.hid_ok = hid_ok
        self.bootstrap_failures = bootstrap_failures
        self.fail = fail
        self.devices = devices
        self.on_call = None

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        if self.on_call:
            self.on_call(cmd)

        def result(rc=0, out="", err=""):
            return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr=err)

        if self.fail and self.fail in " ".join(cmd):
            return result(1, err="boom")
        if cmd[:2] == ["launchctl", "bootstrap"] and self.bootstrap_failures:
            self.bootstrap_failures -= 1
            return result(5, err="Bootstrap failed: 5: Input/output error")
        if "venv" in cmd:
            venv = cmd[-1]
            for rel in (("bin", "python"), ("Scripts", "python.exe"), ("Scripts", "pythonw.exe")):
                os.makedirs(os.path.join(venv, rel[0]), exist_ok=True)
                open(os.path.join(venv, *rel), "w").close()
            self.hid_ok = True
        if cmd[-2:] == ["-c", "import hid"]:
            return result(0 if self.hid_ok else 1)
        if cmd[-1] == "devices":
            return result(self.devices[0], self.devices[1])
        return result()

    def tool(self, name):
        return [c for c in self.calls if c[0] == name]


class FakeRegistry:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


class InstallCase(unittest.TestCase):
    platform = "macos"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # A space and an apostrophe in the path exercise the quoting of the hook commands.
        self.home = os.path.join(tmp.name, "my home's")
        os.makedirs(self.home)
        self.paths = install.Paths(self.home, {}, self.platform, use_venv=False)
        self.run = FakeRun()
        self.registry = FakeRegistry()
        self.spawned = []
        for target, name, value in ((install, "DAEMON_WAIT", 0), (install.time, "sleep", lambda s: None)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def spawn(self, cmd, env=None):
        self.spawned.append(list(cmd))

    def main(self, *args, run=None, env=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = install.main(["--home", self.home, *args], run=run or self.run, platform=self.platform,
                              env=env, spawn=self.spawn, registry=self.registry)
        return rc, out.getvalue(), err.getvalue()

    def install(self, *extra):
        rc, out, err = self.main("--no-venv", "--no-service", *extra)
        self.assertEqual(rc, 0, err)
        return out

    def uninstall(self, *extra):
        rc, out, err = self.main("--uninstall", "--no-venv", "--no-service", *extra)
        self.assertEqual(rc, 0, err)
        return out

    def write(self, path, text, mode=None):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        if mode:
            os.chmod(path, mode)

    def read(self, path):
        with open(path, encoding="utf-8", newline="") as f:
            return f.read()

    def load(self, path):
        return json.loads(self.read(path))

    def ours(self, path):
        """Every handler of ours in a Claude/Codex/Copilot hooks file, as (event, handler)."""
        found = []
        for event, items in self.load(path)["hooks"].items():
            for item in items:
                for handler in item.get("hooks", [item]):
                    if install.is_ours(handler):
                        found.append((event, handler))
        return found


class Registration(InstallCase):
    def test_hooks_are_registered_for_all_three_agents(self):
        out = self.install()
        self.assertEqual(self.read(self.paths.script), self.read(os.path.join(ROOT, "agent_glow.py")))
        for path, specs in ((self.paths.claude, install.CLAUDE_HOOKS), (self.paths.codex, install.CODEX_HOOKS),
                            (self.paths.copilot, install.COPILOT_HOOKS)):
            self.assertEqual(sorted(e for e, _ in self.ours(path)), sorted(s[0] for s in specs), path)
        self.assertIn("/hooks", out)  # Codex has to trust the new hooks
        self.assertIn("Restart running", out)

    def test_every_handler_runs_the_right_action_with_the_right_timeout(self):
        self.install()
        for agent, path, specs, timeout_key in (
                ("claude", self.paths.claude, install.CLAUDE_HOOKS, "timeout"),
                ("codex", self.paths.codex, install.CODEX_HOOKS, "timeout"),
                ("copilot", self.paths.copilot, install.COPILOT_HOOKS, "timeoutSec")):
            found = {}
            for event, handler in self.ours(path):
                command = handler.get("command") or handler["bash"]
                found[event] = (command.split(" hook ")[1].split(" ")[:2], handler[timeout_key])
            expected = {event: ([agent, action], timeout) for event, _m, action, timeout in specs}
            self.assertEqual(found, expected, agent)

    def test_matchers_and_formats(self):
        self.install()
        claude = self.load(self.paths.claude)["hooks"]
        self.assertEqual(claude["PreToolUse"][0]["matcher"], "AskUserQuestion|ExitPlanMode")
        self.assertNotIn("matcher", claude["PermissionRequest"][0])
        self.assertEqual(self.load(self.paths.codex)["hooks"]["PreToolUse"][0]["matcher"], "request_user_input")
        copilot = self.load(self.paths.copilot)
        self.assertEqual(copilot["version"], 1)
        note = copilot["hooks"]["notification"][0]
        self.assertEqual((note["matcher"], note["type"], note["timeoutSec"]),
                         ("permission_prompt|elicitation_dialog", "command", 5))
        self.assertIn(" hook copilot needs-input ", note["bash"])
        self.assertNotIn("powershell", note)
        ask = copilot["hooks"]["preToolUse"][0]
        self.assertEqual(ask["matcher"], "ask_user|exit_plan_mode")
        self.assertIn(" hook copilot needs-input ", ask["bash"])

    def test_posix_commands_are_quoted_and_cannot_fail(self):
        command = install.hook_command(self.paths, "claude", "done")
        self.assertEqual(command, f"{shlex.quote(self.paths.python)} {shlex.quote(self.paths.script)} "
                                  "hook claude done 2>/dev/null || echo '{}'; exit 0")
        self.assertIn("'\"'\"'", command)  # the apostrophe in the home directory

    def test_agents_option_limits_what_is_touched(self):
        out = self.install("--agents", "claude")
        self.assertTrue(os.path.exists(self.paths.claude))
        self.assertFalse(os.path.exists(self.paths.codex))
        self.assertFalse(os.path.exists(self.paths.copilot))
        self.assertNotIn("/hooks", out)
        for bad in ("claude,bogus", ","):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                install.parse_args(["--agents", bad])

    def test_config_locations_follow_the_agents_environment_variables(self):
        paths = install.Paths("/h", {"COPILOT_HOME": "/c", "CODEX_HOME": "/x", "CLAUDE_CONFIG_DIR": "/l"})
        self.assertEqual((paths.copilot, paths.codex, paths.claude),
                         (os.path.join("/c", "hooks", "agent-glow.json"), os.path.join("/x", "hooks.json"),
                          os.path.join("/l", "settings.json")))
        default = install.Paths("/h", {})
        self.assertEqual((default.copilot, default.root),
                         (os.path.join("/h", ".copilot", "hooks", "agent-glow.json"), os.path.join("/h", ".agent-glow")))

    def test_keyboard_option_is_written_to_config(self):
        self.write(self.paths.config, '{\n  "blink_seconds": 0.3,\n  "keyboard": {"lighting": "rgb_matrix"}\n}\n')
        out = self.install("--keyboard", "4D4C:6505")
        self.assertEqual(self.load(self.paths.config), {
            "blink_seconds": 0.3, "keyboard": {"lighting": "rgb_matrix", "vid": "0x4D4C", "pid": "0x6505"}})
        self.assertIn("keyboard: 4d4c:6505 chosen", out)
        cfg = ag.Config(self.load(self.paths.config))
        self.assertEqual((cfg.vid, cfg.pid, cfg.lighting), (0x4D4C, 0x6505, "rgb_matrix"))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            install.parse_args(["--keyboard", "zed65"])

    def test_the_keyboard_check_is_shown(self):
        self.run.devices = (0, "VIA keyboards:\n  * 4d4c:6505  Zed65: ready")
        out = self.install()
        self.assertIn("keyboard: ready", out)
        self.assertIn("            * 4d4c:6505  Zed65: ready", out)
        self.assertIn([self.paths.python, self.paths.script, "devices"], self.run.calls)
        self.run.devices = (1, "No VIA keyboard found.")
        self.assertIn("keyboard: not usable yet", self.install())


class Merging(InstallCase):
    def test_existing_settings_formatting_and_hooks_survive(self):
        original = json.loads(CLAUDE_ORIGINAL)
        original["hooks"] = {"PreToolUse": [USER_HOOK]}
        text = json.dumps(original, indent=2)
        self.write(self.paths.claude, text, 0o600)
        self.install()
        new = self.read(self.paths.claude)
        data = json.loads(new)
        self.assertEqual({k: v for k, v in data.items() if k != "hooks"},
                         {k: v for k, v in original.items() if k != "hooks"})
        self.assertEqual(data["hooks"]["PreToolUse"][0], USER_HOOK)
        self.assertEqual(data["hooks"]["PreToolUse"][1]["matcher"], "AskUserQuestion|ExitPlanMode")
        self.assertFalse(new.endswith("\n"))
        self.assertTrue(new.startswith('{\n  "enabledPlugins"'))
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(os.stat(self.paths.claude).st_mode), 0o600)
        self.assertEqual(self.read(self.paths.claude + install.BACKUP_SUFFIX), text)

    @unittest.skipIf(os.name == "nt", "symlinks need extra rights on Windows")
    def test_settings_behind_a_symlink_are_edited_in_place(self):
        real = os.path.join(self.home, "dotfiles", "claude.json")
        self.write(real, CLAUDE_ORIGINAL)
        os.makedirs(os.path.dirname(self.paths.claude))
        os.symlink(real, self.paths.claude)
        self.install()
        self.assertTrue(os.path.islink(self.paths.claude))
        self.assertIn("hooks", self.load(real))

    def test_installing_twice_changes_nothing_and_keeps_the_first_backup(self):
        self.write(self.paths.claude, CLAUDE_ORIGINAL)
        self.install()
        first = {p: self.read(p) for p in (self.paths.claude, self.paths.codex, self.paths.copilot)}
        self.install()
        self.assertEqual({p: self.read(p) for p in first}, first)
        self.assertEqual(self.read(self.paths.claude + install.BACKUP_SUFFIX), CLAUDE_ORIGINAL)
        events = [e for e, _ in self.ours(self.paths.claude)]
        self.assertEqual(sorted(events), sorted(s[0] for s in install.CLAUDE_HOOKS))

    def test_a_changed_script_path_replaces_the_old_entries(self):
        self.install()
        moved = install.Paths(os.path.join(self.home, "elsewhere"), {}, use_venv=False)
        data = self.load(self.paths.claude)
        install.add_hooks(data, install.grouped_hooks(moved, "claude", install.CLAUDE_HOOKS), "x")
        commands = [h["command"] for groups in data["hooks"].values() for g in groups for h in g["hooks"]]
        self.assertEqual(len(commands), len(install.CLAUDE_HOOKS))
        self.assertTrue(all(shlex.quote(moved.script) in c for c in commands))

    def test_strip_ours_only_touches_our_handlers(self):
        ours = {"type": "command", "command": "'/x/python' '/x/agent_glow.py' hook claude done || true"}
        ours_ps = {"type": "command", "powershell": "& 'C:\\x\\python.exe' 'C:\\x\\agent_glow.py' hook copilot done"}
        theirs = {"type": "command", "command": "echo agent_glow.py is cool"}
        hooks = {"Stop": [{"hooks": [ours, theirs]}, {"hooks": [ours]}, "weird", {"nohooks": 1}],
                 "Other": "not a list", "Empty": [], "OnlyOurs": [{"hooks": [ours, ours_ps]}]}
        install.strip_ours(hooks)
        self.assertEqual(hooks, {"Stop": [{"hooks": [theirs]}, "weird", {"nohooks": 1}],
                                 "Other": "not a list", "Empty": []})

    def test_unusable_config_aborts_before_anything_is_written(self):
        for text, message in (("{not json", "not valid JSON"), ("[1, 2]", "JSON object"),
                              ('{"hooks": []}', "not an object"), ('{"hooks": {"Stop": 5}}', "not a list")):
            self.write(self.paths.claude, text)
            rc, _out, err = self.main("--no-venv", "--no-service")
            self.assertEqual(rc, 1, text)
            self.assertIn(message, err)
            self.assertEqual(self.read(self.paths.claude), text)
            for untouched in (self.paths.root, self.paths.codex, self.paths.copilot):
                self.assertFalse(os.path.exists(untouched), untouched)

    def test_render_json_keeps_the_indent_of_the_original(self):
        data = {"a": {"b": 1}}
        self.assertEqual(install.render_json(data, '{\n\t"x": 1\n}'), '{\n\t"a": {\n\t\t"b": 1\n\t}\n}')
        self.assertEqual(install.render_json(data, '{\n    "x": 1\n}\n'),
                         '{\n    "a": {\n        "b": 1\n    }\n}\n')
        self.assertEqual(install.render_json(data, '{"x":1}'), '{\n  "a": {\n    "b": 1\n  }\n}')
        self.assertEqual(install.render_json(data, None), '{\n  "a": {\n    "b": 1\n  }\n}\n')


class Uninstall(InstallCase):
    def test_original_files_come_back_byte_for_byte(self):
        claude = json.dumps(dict(json.loads(CLAUDE_ORIGINAL), hooks={"Stop": [USER_HOOK]}), indent=2) + "\n"
        codex = json.dumps({"hooks": {"PostToolUse": [USER_HOOK]}}, indent=4)
        self.write(self.paths.claude, claude)
        self.write(self.paths.codex, codex)
        self.install()
        self.assertNotEqual(self.read(self.paths.claude), claude)
        self.uninstall()
        self.assertEqual(self.read(self.paths.claude), claude)
        self.assertEqual(self.read(self.paths.codex), codex)
        for gone in (self.paths.claude + install.BACKUP_SUFFIX, self.paths.codex + install.BACKUP_SUFFIX,
                     self.paths.copilot, self.paths.root):
            self.assertFalse(os.path.exists(gone), gone)

    def test_files_that_did_not_exist_before_are_removed_or_emptied(self):
        self.write(self.paths.claude, CLAUDE_ORIGINAL)
        self.install()
        self.uninstall()
        self.assertEqual(self.read(self.paths.claude), CLAUDE_ORIGINAL)
        self.assertFalse(os.path.exists(self.paths.codex))

    def test_nothing_installed_means_nothing_to_do(self):
        out = self.uninstall()
        self.assertEqual(out, "")
        self.assertEqual(os.listdir(self.home), [])

    def test_settings_changed_since_install_keep_their_backup(self):
        self.write(self.paths.claude, CLAUDE_ORIGINAL)
        self.install()
        data = self.load(self.paths.claude)
        data["newSetting"] = True
        self.write(self.paths.claude, json.dumps(data, indent=2))
        self.uninstall()
        self.assertEqual(self.load(self.paths.claude), dict(json.loads(CLAUDE_ORIGINAL), newSetting=True))
        self.assertTrue(os.path.exists(self.paths.claude + install.BACKUP_SUFFIX))

    def test_a_leftover_lighting_snapshot_is_restored_before_the_script_is_removed(self):
        self.install()
        self.write(os.path.join(self.paths.root, "snapshot.json"), "{}")
        seen = []
        self.run.on_call = lambda cmd: seen.append((list(cmd), os.path.exists(self.paths.script)))
        self.uninstall()
        self.assertEqual(seen, [([self.paths.python, self.paths.script, "restore"], True)])
        self.assertFalse(os.path.exists(self.paths.root))


class Runtime(InstallCase):
    base = getattr(sys, "_base_executable", None) or sys.executable

    def venv_install(self, run, *extra):
        return self.main("--no-service", *extra, run=run)

    def venv(self):
        venv = os.path.join(self.paths.root, "venv")
        return venv, os.path.join(venv, "bin", "python")

    def test_venv_is_built_with_uv_when_available(self):
        with mock.patch.object(install.shutil, "which", return_value="/opt/uv"):
            rc, _out, err = self.venv_install(self.run)
        self.assertEqual(rc, 0, err)
        venv, python = self.venv()
        self.assertEqual(self.run.calls[:2], [["/opt/uv", "venv", "--quiet", "--python", self.base, venv],
                                               ["/opt/uv", "pip", "install", "--quiet", "--python", python,
                                                install.HIDAPI]])
        command = self.ours(self.paths.claude)[0][1]["command"]
        self.assertTrue(command.startswith(shlex.quote(python) + " "), command)

    def test_venv_module_and_pip_are_the_fallback(self):
        with mock.patch.object(install.shutil, "which", return_value=None):
            rc, _out, err = self.venv_install(self.run)
        self.assertEqual(rc, 0, err)
        venv, python = self.venv()
        self.assertEqual(self.run.calls[:2], [[self.base, "-m", "venv", venv],
                                               [python, "-m", "pip", "install", "--quiet",
                                                "--disable-pip-version-check", install.HIDAPI]])

    def test_a_working_venv_is_reused_and_a_broken_one_rebuilt(self):
        _venv, python = self.venv()
        self.write(python, "")
        rc, _out, err = self.venv_install(self.run)
        self.assertEqual(rc, 0, err)
        self.assertFalse([c for c in self.run.calls if "venv" in c or "pip" in c])
        self.write(os.path.join(self.paths.root, "venv", "stale-file"), "")
        broken = FakeRun(hid_ok=False)
        with mock.patch.object(install.shutil, "which", return_value=None):
            rc, _out, err = self.venv_install(broken)
        self.assertEqual(rc, 0, err)
        self.assertTrue(any("venv" in c for c in broken.calls))
        self.assertFalse(os.path.exists(os.path.join(self.paths.root, "venv", "stale-file")))

    def test_a_failing_venv_leaves_the_agent_configs_alone(self):
        self.write(self.paths.claude, CLAUDE_ORIGINAL)
        rc, _out, err = self.venv_install(FakeRun(fail="pip install"))
        self.assertEqual(rc, 1)
        self.assertIn("boom", err)
        self.assertEqual(self.read(self.paths.claude), CLAUDE_ORIGINAL)
        self.assertFalse(os.path.exists(self.paths.copilot))

    def test_a_venv_that_still_cannot_import_hid_is_an_error(self):
        run = FakeRun(hid_ok=False)
        run.on_call = lambda cmd: setattr(run, "hid_ok", False)
        rc, _out, err = self.venv_install(run)
        self.assertEqual(rc, 1)
        self.assertIn("cannot import hid", err)

    def test_the_launcher_runs_the_installed_script(self):
        out = self.install()
        self.assertIn(f"command : {self.paths.launcher}", out)
        self.assertEqual(self.read(self.paths.launcher),
                         f"#!/bin/sh\nexec {shlex.quote(self.paths.python)} {shlex.quote(self.paths.script)} \"$@\"\n")
        if os.name != "nt":
            r = subprocess.run([self.paths.launcher, "version"], capture_output=True, text=True, timeout=15)
            self.assertEqual(r.stdout, f"agent-glow {ag.__version__}\n")

    @unittest.skipIf(os.name == "nt", "the ~/.local/bin link is not made on Windows")
    def test_a_link_is_added_to_local_bin_only_when_it_is_on_path(self):
        local_bin = os.path.join(self.home, ".local", "bin")
        os.makedirs(local_bin)
        link = os.path.join(local_bin, "agent-glow")
        rc, out, err = self.main("--no-venv", "--no-service", env={"PATH": local_bin})
        self.assertEqual(rc, 0, err)
        self.assertEqual(os.readlink(link), self.paths.launcher)
        self.assertIn(f"command : {link}", out)
        rc, _out, err = self.main("--uninstall", "--no-venv", "--no-service", env={"PATH": local_bin})
        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.lexists(link))
        self.write(link, "somebody else's")
        rc, out, err = self.main("--no-venv", "--no-service", env={"PATH": local_bin})
        self.assertEqual(self.read(link), "somebody else's")
        self.assertIn(f"command : {self.paths.launcher}", out)


class LaunchAgent(InstallCase):
    def domain(self):
        return f"gui/{os.getuid() if hasattr(os, 'getuid') else 0}"

    def test_plist_and_launchctl_calls(self):
        rc, out, err = self.main("--no-venv")
        self.assertEqual(rc, 0, err)
        with open(self.paths.plist, "rb") as f:
            plist = plistlib.load(f)
        self.assertEqual(plist["Label"], "io.github.f.agent-glow")
        self.assertEqual(plist["ProgramArguments"], [self.paths.python, self.paths.script, "daemon"])
        self.assertTrue(plist["RunAtLoad"])
        self.assertEqual(plist["KeepAlive"], {"SuccessfulExit": False})
        self.assertEqual(plist["StandardErrorPath"], self.paths.service_log)
        label = install.LAUNCHD_LABEL
        self.assertEqual(self.run.tool("launchctl"), [["launchctl", "bootout", f"{self.domain()}/{label}"],
                                                      ["launchctl", "bootstrap", self.domain(), self.paths.plist]])
        self.assertIn("not running yet", out)  # the fake launchd never starts a daemon

    def test_the_daemon_pid_is_reported_once_it_runs(self):
        with mock.patch.object(install.agent_glow, "daemon_pid", return_value=4242):
            rc, out, err = self.main("--no-venv")
        self.assertEqual(rc, 0, err)
        self.assertIn("running (pid 4242)", out)

    def test_uninstall_stops_the_daemon_and_removes_the_plist(self):
        self.main("--no-venv")
        self.run.calls.clear()
        rc, out, err = self.main("--uninstall", "--no-venv")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.run.tool("launchctl"),
                         [["launchctl", "bootout", f"{self.domain()}/{install.LAUNCHD_LABEL}"]])
        self.assertFalse(os.path.exists(self.paths.plist))
        self.assertIn("LaunchAgent io.github.f.agent-glow removed", out)

    def test_bootstrap_is_retried_while_the_old_instance_shuts_down(self):
        run = FakeRun(bootstrap_failures=2)
        rc, _out, err = self.main("--no-venv", run=run)
        self.assertEqual(rc, 0, err)
        self.assertEqual(sum(c[1] == "bootstrap" for c in run.tool("launchctl")), 3)

    def test_bootstrap_that_never_works_is_reported(self):
        rc, _out, err = self.main("--no-venv", run=FakeRun(bootstrap_failures=99))
        self.assertEqual(rc, 1)
        self.assertIn("launchctl bootstrap failed", err)
        self.assertIn("Input/output error", err)

    def test_no_service_touches_neither_launchctl_nor_the_plist(self):
        out = self.install()
        self.assertEqual(self.run.tool("launchctl"), [])
        self.assertFalse(os.path.exists(self.paths.plist))
        self.assertIn("--no-service", out)


class SystemdService(InstallCase):
    platform = "linux"

    def setUp(self):
        super().setUp()
        self.which = {"systemctl": "/usr/bin/systemctl"}
        patcher = mock.patch.object(install.shutil, "which", lambda name: self.which.get(name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def systemctl(self):
        return [c[2:] for c in self.run.tool("systemctl")]

    def test_unit_file_and_systemctl_calls(self):
        rc, out, err = self.main("--no-venv")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.paths.unit,
                         os.path.join(self.home, ".config", "systemd", "user", "agent-glow.service"))
        unit = self.read(self.paths.unit)
        self.assertIn(f"ExecStart={install.systemd_quote(self.paths.python)} "
                      f"{install.systemd_quote(self.paths.script)} \"daemon\"\n", unit)
        self.assertIn("Restart=on-failure\n", unit)
        self.assertIn("WantedBy=default.target\n", unit)
        self.assertEqual(self.systemctl(), [["daemon-reload"], ["enable", "agent-glow.service"],
                                            ["restart", "agent-glow.service"]])
        self.assertIn("systemd user service agent-glow.service", out)
        self.assertNotIn("udev", out)

    def test_systemd_quoting(self):
        self.assertEqual(install.systemd_quote('/a b/"q"/100%/x\\y'), '"/a b/\\"q\\"/100%%/x\\\\y"')

    def test_xdg_config_home_is_respected(self):
        paths = install.Paths("/h", {"XDG_CONFIG_HOME": "/cfg"}, "linux")
        self.assertEqual(paths.unit, os.path.join("/cfg", "systemd", "user", "agent-glow.service"))

    def test_a_reinstall_stops_the_running_service_first(self):
        self.main("--no-venv")
        self.run.calls.clear()
        self.main("--no-venv")
        self.assertEqual(self.systemctl()[0], ["stop", "agent-glow.service"])

    def test_without_systemd_everything_else_is_still_installed(self):
        self.which = {}
        rc, out, err = self.main("--no-venv")
        self.assertEqual(rc, 0, err)
        self.assertIn("systemctl was not found", out)
        self.assertIn(f"run `{self.paths.launcher} daemon` at login yourself", out)
        self.assertTrue(self.ours(self.paths.claude))

    def test_a_failing_systemctl_is_reported_but_not_fatal(self):
        rc, out, err = self.main("--no-venv", run=FakeRun(fail="enable"))
        self.assertEqual(rc, 0, err)
        self.assertIn("`systemctl --user enable agent-glow.service` failed: boom", out)

    def test_uninstall_disables_and_removes_the_unit(self):
        self.main("--no-venv")
        self.run.calls.clear()
        rc, out, err = self.main("--uninstall", "--no-venv")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.systemctl(), [["disable", "--now", "agent-glow.service"], ["daemon-reload"]])
        self.assertFalse(os.path.exists(self.paths.unit))

    def test_udev_instructions_when_the_keyboard_cannot_be_opened(self):
        self.run.devices = (1, "    4d4c:6505  Zed65: cannot open it: open failed (on Linux: see ...)")
        rc, out, err = self.main("--no-venv")
        self.assertEqual(rc, 0, err)
        self.assertIn(f"{self.paths.launcher} udev-rule | sudo tee /etc/udev/rules.d/60-agent-glow.rules", out)
        self.assertIn("unplug and replug", out)


class WindowsInstall(InstallCase):
    platform = "windows"

    def test_venv_and_launcher_use_windows_layout(self):
        with mock.patch.object(install.shutil, "which", return_value=None):
            rc, out, err = self.main("--no-service")
        self.assertEqual(rc, 0, err)
        paths = install.Paths(self.home, {}, "windows")
        self.assertEqual(paths.python, os.path.join(paths.root, "venv", "Scripts", "python.exe"))
        self.assertEqual(paths.pythonw, os.path.join(paths.root, "venv", "Scripts", "pythonw.exe"))
        self.assertIn([paths.python, "-c", "import hid"], self.run.calls)
        self.assertTrue(paths.launcher.endswith("agent-glow.cmd"))
        self.assertEqual(self.read(paths.launcher), f'@"{paths.python}" "{paths.script}" %*\r\n')
        self.assertIsNone(paths.link)

    def test_each_agent_gets_the_command_form_its_shell_understands(self):
        self.install()
        p = self.paths
        codex = self.ours(p.codex)
        self.assertIn(("Stop", {"type": "command", "timeout": 5,
                                "command": f'"{p.python}" "{p.script}" hook codex done 2>NUL || echo {{}}'}), codex)
        copilot = dict(self.ours(p.copilot))["agentStop"]
        self.assertNotIn("bash", copilot)
        quoted = [install.ps_quote(x) for x in (p.python, p.script)]
        self.assertEqual(copilot["powershell"], f"& {quoted[0]} {quoted[1]} hook copilot done 2>$null; exit 0")
        self.assertIn("''", quoted[1])  # the apostrophe in the home directory, PowerShell style
        claude = dict(self.ours(p.claude))["Stop"]["command"]
        self.assertEqual(claude, install.sh_command(p.python.replace("\\", "/"), p.script.replace("\\", "/"),
                                                    "hook claude done"))
        self.assertNotIn("\\", claude.split(" hook ")[0])

    def test_the_daemon_starts_now_and_at_login(self):
        rc, out, err = self.main("--no-venv")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.registry.values,
                         {"agent-glow": f'"{self.paths.pythonw}" "{self.paths.script}" daemon'})
        self.assertEqual(self.spawned, [[self.paths.pythonw, self.paths.script, "daemon"]])
        self.assertIn("HKCU Run key", out)

    def test_reinstall_and_uninstall_stop_the_daemon_with_the_stop_command(self):
        self.main("--no-venv")
        self.run.calls.clear()
        stop = [self.paths.python, self.paths.script, "stop"]
        self.main("--no-venv")
        self.assertEqual(self.run.calls[0], stop)
        self.run.calls.clear()
        rc, out, err = self.main("--uninstall", "--no-venv")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.run.calls[0], stop)
        self.assertEqual(self.registry.values, {})
        self.assertFalse(os.path.exists(self.paths.root))


class HookCommands(InstallCase):
    """Run the registered commands through the shells the agents use on this OS."""

    platform = REAL_PLATFORM

    def shell(self, agent, handler):
        if os.name != "nt":
            return ["/bin/sh", "-c", handler.get("command") or handler["bash"]]
        if agent == "codex":
            return f'cmd.exe /C "{handler["command"]}"'  # a string is passed to CreateProcess verbatim
        if agent == "copilot":
            return ["powershell", "-NoProfile", "-NonInteractive", "-Command", handler["powershell"]]
        return [GIT_BASH, "-c", handler["command"]]

    def commands(self):
        out = []
        for agent, path in (("claude", self.paths.claude), ("codex", self.paths.codex),
                            ("copilot", self.paths.copilot)):
            if agent == "claude" and os.name == "nt" and not os.path.exists(GIT_BASH):
                continue
            out += [(agent, event, handler) for event, handler in self.ours(path)]
        return out

    def run_shell(self, agent, handler, state, payload=b"{}"):
        env = dict(os.environ, AGENT_GLOW_HOME=state)
        return subprocess.run(self.shell(agent, handler), input=payload, capture_output=True, env=env, timeout=30)

    def state(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return tmp.name

    def test_every_command_exits_zero_and_prints_json(self):
        self.install()
        state = self.state()
        commands = self.commands()
        self.assertGreaterEqual(len(commands), len(install.CODEX_HOOKS) + len(install.COPILOT_HOOKS))
        for agent, event, handler in commands:
            r = self.run_shell(agent, handler, state, b'{"session_id": "s", "tool_name": "Bash"}')
            self.assertEqual((r.returncode, r.stdout.strip()), (0, b"{}"), (agent, event, handler, r.stderr))

    def test_commands_drive_the_marker_lifecycle(self):
        self.install()
        by_event = dict(self.ours(self.paths.codex))
        state = self.state()
        store = ag.Store(state)
        payload = b'{"session_id": "s1", "tool_name": "Bash"}'
        self.run_shell("codex", by_event["PermissionRequest"], state, payload)
        self.assertEqual([m["state"] for _p, m in store.markers()], ["needs_input"])
        self.run_shell("codex", by_event["PostToolUse"], state, b'{"session_id": "s1", "tool_name": "Read"}')
        self.assertEqual(len(store.markers()), 1)  # another tool finishing is not the answer
        self.run_shell("codex", by_event["PostToolUse"], state, payload)
        self.assertEqual(store.markers(), [])
        self.run_shell("codex", by_event["Stop"], state, b'{"session_id": "s1"}')
        self.assertEqual([m["state"] for _p, m in store.markers()], ["done"])
        self.run_shell("codex", by_event["UserPromptSubmit"], state, b'{"session_id": "s1"}')
        self.assertEqual(store.markers(), [])

    def test_copilot_question_marks_the_session_as_waiting(self):
        self.install()
        handler = dict(self.ours(self.paths.copilot))["preToolUse"]
        state = self.state()
        self.run_shell("copilot", handler, state, b'{"sessionId": "c1", "toolName": "ask_user"}')
        self.assertEqual([(m["agent"], m["state"]) for _p, m in ag.Store(state).markers()],
                         [("copilot", "needs_input")])

    def test_a_missing_python_or_script_never_fails_the_agent(self):
        self.install()
        state = self.state()
        missing = install.Paths(os.path.join(self.home, "nowhere"), {}, self.platform, use_venv=False)
        missing.python = os.path.join(self.home, "nonexistent", "python")
        os.unlink(self.paths.script)
        for broken in (missing, self.paths):
            for agent in ("codex", "copilot"):
                handler = install.copilot_config(broken)["hooks"]["agentStop"][0] if agent == "copilot" else \
                    {"command": install.hook_command(broken, "codex", "done")}
                r = self.run_shell(agent, handler, state)
                self.assertEqual(r.returncode, 0, (agent, r.stderr))
                if os.name != "nt" or agent == "codex":
                    self.assertEqual(r.stdout.strip(), b"{}", agent)
                if os.name != "nt":
                    self.assertEqual(r.stderr, b"")


class Cli(unittest.TestCase):
    def test_install_and_uninstall_as_a_program(self):
        with tempfile.TemporaryDirectory() as home:
            base = [sys.executable, os.path.join(ROOT, "install.py"), "--home", home, "--no-venv", "--no-service"]
            r = subprocess.run(base, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Next steps", r.stdout)
            self.assertTrue(os.path.exists(os.path.join(home, ".copilot", "hooks", "agent-glow.json")))
            r = subprocess.run(base + ["--uninstall"], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(os.path.exists(os.path.join(home, ".agent-glow")))
            self.assertFalse(os.path.exists(os.path.join(home, ".copilot", "hooks", "agent-glow.json")))

    def test_help(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "install.py"), "--help"], capture_output=True,
                           text=True, timeout=30)
        self.assertEqual(r.returncode, 0)
        self.assertIn("--uninstall", r.stdout)


if __name__ == "__main__":
    unittest.main()
