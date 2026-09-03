"""
Unit tests for auto-registration of API functions after a script runs.

_extract_api_functions works on source text, so it also matches calls that
never executed. sap_executor gates registration on _com_path_exists, which
resolves each path against the live COM object. These tests stub the bridge
so the gate can be checked without SAP2000.

Run with: python -m pytest mcp_server/tests/test_autoregister.py -v
"""

import sys
from pathlib import Path

# Add mcp_server to path so imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import sap_executor


class _Leaf:
    """Stands in for a COM object exposing a fixed set of members."""

    def __init__(self, members: dict | None = None):
        for name, value in (members or {}).items():
            setattr(self, name, value)


# sap_object, sap_model and is_connected are read-only properties backed by
# these private attributes, so tests patch the attributes and let the real
# property logic run.
def _wire_bridge(monkeypatch, sap_object, sap_model):
    monkeypatch.setattr(sap_executor.bridge, "_sap_object", sap_object, raising=False)
    monkeypatch.setattr(sap_executor.bridge, "_sap_model", sap_model, raising=False)


@pytest.fixture
def fake_model(monkeypatch):
    """Install a stub SapModel whose member set is known and small."""
    model = _Leaf({
        "InitializeNewModel": lambda: 0,
        "File": _Leaf({"NewBlank": lambda: 0}),
        "Func": _Leaf({"GetValues": lambda *a: 0, "FuncRS": _Leaf({"SetUser": lambda *a: 0})}),
    })
    sap_object = _Leaf({"Hide": lambda: 0})
    _wire_bridge(monkeypatch, sap_object, model)
    return model


class TestExtractApiFunctions:
    """The regex scanner sees text, not execution."""

    def test_finds_calls(self):
        script = "SapModel.File.NewBlank()\nSapObject.Hide()"
        assert sap_executor._extract_api_functions(script) == [
            "SapModel.File.NewBlank",
            "SapObject.Hide",
        ]

    def test_deduplicates(self):
        script = "SapModel.File.NewBlank()\nSapModel.File.NewBlank()"
        assert sap_executor._extract_api_functions(script) == ["SapModel.File.NewBlank"]

    def test_matches_calls_that_never_ran(self):
        """This is the bug the COM gate exists to contain."""
        script = "try:\n    SapModel.Nope.Missing()\nexcept Exception:\n    pass"
        assert "SapModel.Nope.Missing" in sap_executor._extract_api_functions(script)


class TestComPathExists:
    """The gate itself."""

    def test_real_nested_path(self, fake_model):
        assert sap_executor._com_path_exists("SapModel.Func.FuncRS.SetUser") is True

    def test_real_top_level_path(self, fake_model):
        assert sap_executor._com_path_exists("SapModel.InitializeNewModel") is True

    def test_sap_object_path(self, fake_model):
        assert sap_executor._com_path_exists("SapObject.Hide") is True

    def test_missing_leaf_method(self, fake_model):
        """Func.FuncRS exists but has no GetValues — the real-world case."""
        assert sap_executor._com_path_exists("SapModel.Func.FuncRS.GetValues") is False

    def test_missing_intermediate_member(self, fake_model):
        assert sap_executor._com_path_exists("SapModel.Vehicle.GetNameList") is False

    def test_bare_path_defaults_to_sap_model(self, fake_model):
        assert sap_executor._com_path_exists("InitializeNewModel") is True

    def test_false_when_not_connected(self, monkeypatch):
        _wire_bridge(monkeypatch, None, None)
        assert sap_executor.bridge.is_connected is False
        assert sap_executor._com_path_exists("SapModel.InitializeNewModel") is False

    def test_false_when_root_is_none(self, monkeypatch):
        """Connected, but the model reference was never populated."""
        _wire_bridge(monkeypatch, _Leaf(), None)
        assert sap_executor.bridge.is_connected is True
        assert sap_executor._com_path_exists("SapModel.InitializeNewModel") is False

    def test_false_when_com_raises(self, monkeypatch):
        """A COM failure must not be mistaken for a resolved path."""

        class _Angry:
            def __getattr__(self, name):
                raise OSError("COM error -2147417846")

        _wire_bridge(monkeypatch, _Leaf(), _Angry())
        assert sap_executor._com_path_exists("SapModel.Anything") is False


class TestGateSplitsCandidates:
    """A script mixing real and unresolvable paths registers only the real ones."""

    def test_only_real_paths_pass_the_gate(self, fake_model):
        script = (
            "SapModel.File.NewBlank()\n"
            "try:\n"
            "    SapModel.Func.FuncRS.GetValues('X', 0, [], [])\n"
            "except Exception:\n"
            "    pass\n"
        )
        candidates = sap_executor._extract_api_functions(script)
        registered = [p for p in candidates if sap_executor._com_path_exists(p)]
        unresolved = [p for p in candidates if not sap_executor._com_path_exists(p)]

        assert registered == ["SapModel.File.NewBlank"]
        assert unresolved == ["SapModel.Func.FuncRS.GetValues"]
