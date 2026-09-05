"""Guards for child identity versus IoT Hub command routing."""
from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "zentraly"
ENTITY_FILES = ("climate.py", "lock.py", "number.py", "select.py")
COMMAND_METHODS = {
    "set_target_temperature",
    "turn_on",
    "turn_off",
    "set_zttin01_target_temperature",
    "set_zttin01_mode",
    "set_zttin01_lock",
}


def _source(name: str) -> str:
    return (COMPONENT / name).read_text(encoding="utf-8")


class CommandRoutingSourceTests(unittest.TestCase):
    """Every entity keeps its child ID but routes cloud commands to its parent."""

    def test_command_entities_resolve_the_shared_target_once(self) -> None:
        for name in ENTITY_FILES:
            with self.subTest(file=name):
                self.assertIn(
                    "self._command_device_id = command_device_id(device)",
                    _source(name),
                )

    def test_entity_cloud_calls_never_use_child_identity(self) -> None:
        for name in ENTITY_FILES:
            tree = ast.parse(_source(name), filename=name)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr not in COMMAND_METHODS or not node.args:
                    continue
                first = node.args[0]
                with self.subTest(file=name, method=node.func.attr, line=node.lineno):
                    self.assertFalse(
                        isinstance(first, ast.Attribute)
                        and isinstance(first.value, ast.Name)
                        and first.value.id == "self"
                        and first.attr == "_device_serial"
                    )

    def test_direct_entity_readbacks_receive_the_command_target(self) -> None:
        for name in ("lock.py", "number.py", "select.py"):
            tree = ast.parse(_source(name), filename=name)
            calls = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "refresh_zttin01_after_write"
            ]
            self.assertTrue(calls, name)
            for call in calls:
                keywords = {keyword.arg for keyword in call.keywords}
                with self.subTest(file=name, line=call.lineno):
                    self.assertIn("command_device_id", keywords)

    def test_coordinator_enrichment_uses_shared_command_routing(self) -> None:
        source = _source("__init__.py")
        self.assertIn("command_target = command_device_id(device)", source)
        self.assertIn("read_zttin01_raw_attrs(\n                    command_target,", source)
        self.assertIn("read_boiler_raw_attrs(\n                    command_target,", source)


if __name__ == "__main__":
    unittest.main()
