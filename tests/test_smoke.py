import importlib
import unittest

MODULES = [
    "kivu",
    "kivu.cli",
    "kivu.config",
    "kivu.messages",
    "kivu.ui",
    "kivu.tracing",
    "kivu.session",
    "kivu.agent.loop",
    "kivu.agent.context",
    "kivu.providers.base",
    "kivu.tools.registry",
    "kivu.tools.files",
    "kivu.tools.shell",
    "kivu.sandbox.policy",
    "kivu.sandbox.approval",
]


class SmokeTest(unittest.TestCase):
    def test_modules_import(self) -> None:
        for name in MODULES:
            with self.subTest(module=name):
                importlib.import_module(name)


if __name__ == "__main__":
    unittest.main()
