"""Fixture-only Task Knowledge Manifest Integration boundary."""

from .version import COMPONENT_VERSION
from .fixture_flow import run_fixture_flow

__all__ = ("COMPONENT_VERSION", "run_fixture_flow")
