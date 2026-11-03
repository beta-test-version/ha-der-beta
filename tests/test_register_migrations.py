"""Check entity identity preservation after protocol address corrections."""

import ast
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_device_identity import COMPONENT, load_methods


class RegisterMigrationTests(unittest.TestCase):
    def test_error_code_10_address_and_localized_names(self):
        registers = load_methods("registers_schema.py", {})["REGISTERS"]
        reg = next(r for r in registers if r["name"] == "Device error code 10")
        self.assertEqual(reg["offset"], 0x201A)
        self.assertEqual(40001 + reg["offset"], 48219)
        self.assertEqual(reg["dtype"], "UINT16")
        self.assertNotIn(0x2020, {r["offset"] for r in registers})
        for path in [COMPONENT / "strings.json", *(COMPONENT / "translations").glob("*.json")]:
            sensors = json.loads(path.read_text(encoding="utf-8"))["entity"]["sensor"]
            self.assertNotIn("reg_2020", sensors)
            self.assertTrue(sensors["reg_201a"]["name"].startswith("D1. "))

    def migration(self, identities):
        registry = Mock()
        registry.async_get_entity_id.side_effect = (
            lambda domain, platform, unique_id: identities.get(unique_id)
        )

        def update(entity_id, *, new_unique_id, translation_key):
            old_key = next(key for key, value in identities.items() if value == entity_id)
            del identities[old_key]
            identities[new_unique_id] = entity_id

        registry.async_update_entity.side_effect = update
        tree = ast.parse((COMPONENT / "__init__.py").read_text(encoding="utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == "_migrate_error_code_entity")
        namespace = {"er": SimpleNamespace(async_get=lambda hass: registry),
                     "DOMAIN": "ecoflow_modbus"}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "__init__.py", "exec"), namespace)
        return namespace["_migrate_error_code_entity"], registry

    def test_existing_entity_is_preserved_and_migration_is_idempotent(self):
        identities = {"entry-2020": "sensor.my_error_10", "other-2020": "sensor.other"}
        migrate, registry = self.migration(identities)
        migrate(None, SimpleNamespace(entry_id="entry"))
        migrate(None, SimpleNamespace(entry_id="entry"))
        self.assertEqual(identities, {"entry-201A": "sensor.my_error_10",
                                     "other-2020": "sensor.other"})
        registry.async_update_entity.assert_called_once_with(
            "sensor.my_error_10", new_unique_id="entry-201A", translation_key="reg_201a"
        )

    def test_existing_new_identity_is_not_overwritten(self):
        migrate, registry = self.migration({"entry-2020": "sensor.old",
                                            "entry-201A": "sensor.new"})
        migrate(None, SimpleNamespace(entry_id="entry"))
        registry.async_update_entity.assert_not_called()
