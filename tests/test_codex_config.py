"""Codex 配置层级、实际指令路径及重复部署的回归测试。"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import tomllib


SOURCE = Path(__file__).resolve().parents[1] / "engine" / "破甲一键通.py"
SPEC = importlib.util.spec_from_file_location("pojia", SOURCE)
pojia = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pojia)


class CodexConfigTests(unittest.TestCase):
    def setUp(self):
        self.target = pojia.CodexTarget()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.args = pojia.build_parser().parse_args([
            "--target", "codex", "--codex-dir", str(self.home), "--yes"])
        self.config = self.home / "config.toml"
        self.instructions = self.home / pojia.CODEX_INSTR
        persona, _ = pojia.load_user_persona()
        _, self.want = pojia.build_policy(persona)
        self.body = self.target.build_instructions(persona, self.want)
        self.log_patch = mock.patch.object(pojia, "log")
        self.log_patch.start()
        self.addCleanup(self.log_patch.stop)
        # HERE 指向临时目录：apply 现在会把"本脚本这一版"同步进项目的
        # codex/<CODEX_INSTR> 快照，测试必须落在沙箱里，别碰项目文件。
        self.here_patch = mock.patch.object(pojia, "HERE", self.temp.name)
        self.here_patch.start()
        self.addCleanup(self.here_patch.stop)
        self.proc_patch = mock.patch.object(pojia, "find_processes", return_value=[])
        self.proc_patch.start()
        self.addCleanup(self.proc_patch.stop)

    def legacy_config(self):
        return ('model = "example"\n[model_providers.example]\nname = "Example"\n\n'
                + pojia.MARK_BEGIN + '\nmodel_instructions_file = "./'
                + pojia.CODEX_INSTR + '"\n' + pojia.MARK_END + '\n')

    def write_current_install(self):
        self.config.write_text(self.legacy_config(), encoding="utf-8")
        self.instructions.write_text(self.body, encoding="utf-8")

    def test_moves_legacy_setting_to_root_preserving_provider(self):
        updated = self.target.patch_config(self.legacy_config(), "./" + pojia.CODEX_INSTR)
        config = tomllib.loads(updated)
        self.assertEqual(config["model_instructions_file"], "./" + pojia.CODEX_INSTR)
        self.assertEqual(config["model_providers"]["example"], {"name": "Example"})
        self.assertEqual(config["model"], "example")
        self.assertEqual(updated, self.target.patch_config(updated, "./" + pojia.CODEX_INSTR))

    def test_replaces_existing_root_without_touching_profile(self):
        original = ('"model_instructions_file" = "old.md" # keep other settings\n'
                    '[profiles.work]\nmodel_instructions_file = "profile.md"\n')
        updated = self.target.patch_config(original, "new.md")
        config = tomllib.loads(updated)
        self.assertEqual(config["model_instructions_file"], "new.md")
        self.assertEqual(config["profiles"]["work"]["model_instructions_file"], "profile.md")

    def test_multiline_values_are_not_mistaken_for_tables_or_settings(self):
        original = ('developer_instructions = """\n[example]\n'
                    'model_instructions_file = "fake.md"\n"""\n'
                    'items = [\n["nested array"],\n]\n'
                    'model_instructions_file = "old.md"\n[features]\nexample = true\n')
        updated = self.target.patch_config(original, "new.md")
        before, after = tomllib.loads(original), tomllib.loads(updated)
        self.assertEqual(after.pop("model_instructions_file"), "new.md")
        before.pop("model_instructions_file")
        self.assertEqual(before, after)

    def test_empty_crlf_and_windows_paths(self):
        for original in ("", self.legacy_config().replace("\n", "\r\n")):
            with self.subTest(original=bool(original)):
                path = 'C:\\Users\\测试\\my "instructions".md'
                updated = self.target.patch_config(original, path)
                self.assertEqual(tomllib.loads(updated)["model_instructions_file"], path)

    def test_nested_setting_is_not_effective(self):
        self.assertEqual(self.target.current_instr(self.legacy_config()), "")

    def test_python_38_fallback_ignores_nested_and_multiline_content(self):
        config = ('developer_instructions = """\nmodel_instructions_file = "fake.md"\n"""\n'
                  "model_instructions_file = 'current.md' # comment\n"
                  '[profiles.work]\nmodel_instructions_file = "profile.md"\n')
        with mock.patch.dict("sys.modules", {"tomllib": None}):
            self.assertEqual(self.target.current_instr(config), "current.md")
            self.assertEqual(self.target.current_instr(self.legacy_config()), "")

    def test_misplaced_deployment_is_reported_stale(self):
        self.write_current_install()
        _, rows = self.target.status_rows(self.args)
        self.assertTrue(any(row[0] == "stale" for row in rows))
        self.assertFalse(any(row[0] == "loose" for row in rows))

    def test_history_cannot_mask_missing_live_config(self):
        # A previous apply is only historical evidence.  If a desktop config
        # writer removed the live key, status must call it stale instead of
        # reporting the deployment as active.
        self.instructions.write_text(self.body, encoding="utf-8")
        self.target.save_state(self.home, {
            "last_action": "apply",
            "instructions_file": pojia.CODEX_INSTR,
        })
        _, rows = self.target.status_rows(self.args)
        self.assertTrue(any(row[0] == "stale" for row in rows))
        self.assertFalse(any(row[0] == "loose" for row in rows))

    def test_checks_actual_referenced_file(self):
        self.write_current_install()
        self.config.write_text(self.target.patch_config("", "elsewhere/" + pojia.CODEX_INSTR), encoding="utf-8")
        _, rows = self.target.status_rows(self.args)
        self.assertTrue(any(row[0] == "strict" for row in rows))
        self.assertFalse(any(row[0] == "loose" for row in rows))

    def test_current_version_still_repairs_config_and_backs_up(self):
        self.write_current_install()
        original = self.config.read_text(encoding="utf-8")
        self.assertEqual(self.target.apply(self.args, "apply"), {"fixed": 1})
        self.assertEqual(tomllib.loads(self.config.read_text(encoding="utf-8"))["model_instructions_file"], "./" + pojia.CODEX_INSTR)
        state = json.loads((self.home / pojia.CODEX_STATE).read_text(encoding="utf-8"))
        self.assertEqual(Path(state["backup_config"]).read_text(encoding="utf-8"), original)
        self.assertEqual(state["hash_mark"], pojia.HASH_MARK + self.want)
        _, rows = self.target.status_rows(self.args)
        # 三条 loose：指令文件 / config 顶层项 / AGENTS.md 标记块（双线注入）
        self.assertEqual(sum(row[0] == "loose" for row in rows), 3)
        before = {p.name: p.stat().st_mtime_ns for p in self.home.iterdir()}
        self.assertEqual(self.target.apply(self.args, "apply"), {"skip": 1})
        self.assertEqual(before, {p.name: p.stat().st_mtime_ns for p in self.home.iterdir()})

    def test_does_not_record_success_when_config_is_overwritten_during_write(self):
        self.write_current_install()
        expected = "./" + pojia.CODEX_INSTR
        with mock.patch.object(self.target, "current_instr",
                               side_effect=["", expected, ""]):
            result = self.target.apply(self.args, "apply")
        self.assertEqual(result, {"err": 1, "fixed": 0})
        self.assertFalse((self.home / pojia.CODEX_STATE).exists())

    def test_dry_run_leaves_config_and_instructions_unchanged(self):
        self.write_current_install()
        before = {p.name: p.read_bytes() for p in self.home.iterdir()}
        self.assertEqual(self.target.apply(self.args, "dry-run")["fixed"], 1)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.home.iterdir()})

    def test_other_persona_is_preserved_while_fixing_config(self):
        self.write_current_install()
        other_body = self.body.replace(self.want, "000000000000")
        self.instructions.write_text(other_body, encoding="utf-8")
        self.assertEqual(self.target.apply(self.args, "apply"), {"fixed": 1})
        self.assertEqual(self.instructions.read_text(encoding="utf-8"), other_body)
        state = json.loads((self.home / pojia.CODEX_STATE).read_text(encoding="utf-8"))
        self.assertEqual(state["hash_mark"], pojia.HASH_MARK + "000000000000")

    def test_malformed_config_is_not_overwritten(self):
        self.write_current_install()
        self.config.write_text('model = "unterminated\n', encoding="utf-8")
        before = self.config.read_bytes()
        with self.assertRaises(ValueError):
            self.target.apply(self.args, "apply")
        self.assertEqual(self.config.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
