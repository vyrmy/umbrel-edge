"""The desktop slots' egress proxy configuration (decisions 17 and 18)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SLOTS = sorted(ROOT.glob("vyrmy-desktop-*/docker-compose.yml"))


def _compose(path: Path) -> dict[str, Any]:
    doc: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return doc


def _squid_lines(compose: dict[str, Any]) -> list[str]:
    script = compose["services"]["egress"]["command"][0]
    return [line.strip() for line in script.splitlines()]


def test_every_slot_is_checked() -> None:
    assert len(SLOTS) >= 2


@pytest.mark.parametrize("path", SLOTS, ids=lambda p: p.parent.name)
def test_no_ipv4_mapped_range_in_the_deny_lists(path: Path) -> None:
    # With IPv6 in the container, Squid holds IPv4 addresses as ::ffff:a.b.c.d, so a
    # ::ffff:0:0/96 entry refused every site (Desktop 2026.10.29.3).
    for line in _squid_lines(_compose(path)):
        if line.startswith("acl ") and " dst " in line:
            assert "::ffff" not in line.lower(), line


@pytest.mark.parametrize("path", SLOTS, ids=lambda p: p.parent.name)
def test_addresses_agree_within_the_slot(path: Path) -> None:
    n = re.fullmatch(r"vyrmy-desktop-(\d+)", path.parent.name)
    assert n
    compose = _compose(path)
    desktop = compose["services"]["desktop"]
    egress = compose["services"]["egress"]
    env = desktop["environment"]
    assert desktop["networks"]["desk"]["ipv4_address"] == f"10.31.{n[1]}.10"
    assert egress["networks"]["desk"]["ipv4_address"] == f"10.31.{n[1]}.3"
    assert env["EGRESS_PROXY"] == f"http://10.31.{n[1]}.3:3128"
    assert env["EGRESS_DIRECT_HOST"] == f"10.31.{n[1]}.2"
    assert f"acl clients src 10.31.{n[1]}.10/32" in _squid_lines(compose)
    assert compose["networks"]["desk"]["name"] == f"vyrmy-edge_desk-{n[1]}"
