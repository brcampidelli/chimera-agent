"""One range of internal addresses was not on the SSRF guard's list, and it is a metadata endpoint.

`100.64.0.0/10` is RFC 6598 carrier-grade NAT space. Python's `ipaddress` does not report it as
private on the interpreters this project runs, so `_is_blocked` — which is otherwise strong, and
catches `::ffff:` mapping, octal and decimal literals that the obvious implementations miss — waved
it through.

The address that matters inside it is `100.100.100.200`: Alibaba Cloud's instance metadata service,
the same role `169.254.169.254` plays on AWS and GCP, and the module's own docstring names that one
as the thing it exists to stop.

The tests below keep the ranges the module already got right, because a fix to a blocklist is the
kind of change that quietly narrows one while widening another.
"""

from __future__ import annotations

import ipaddress

import pytest

from chimera.scrape.ssrf import _is_blocked

BLOQUEAR = [
    ("100.100.100.200", "metadata da Alibaba Cloud — o motivo deste conserto"),
    ("100.64.0.1", "a borda de baixo da faixa CGNAT"),
    ("100.127.255.254", "a borda de cima da faixa CGNAT"),
    ("169.254.169.254", "metadata da AWS/GCP — o caso que o docstring nomeia"),
    ("127.0.0.1", "loopback"),
    ("10.0.0.1", "privado classe A"),
    ("192.168.1.1", "privado classe C"),
    ("::1", "loopback IPv6"),
    ("::ffff:a9fe:a9fe", "metadata via mapeamento IPv4-em-IPv6"),
    ("0.0.0.0", "não especificado"),
]

PERMITIR = [
    ("100.63.255.255", "um endereço ANTES da faixa CGNAT — público de verdade"),
    ("100.128.0.1", "um endereço DEPOIS da faixa CGNAT — público de verdade"),
    ("8.8.8.8", "DNS público"),
    ("1.1.1.1", "DNS público"),
    ("93.184.216.34", "um host qualquer da internet"),
]


@pytest.mark.parametrize(("endereco", "porque"), BLOQUEAR)
def test_an_internal_address_is_blocked(endereco: str, porque: str) -> None:
    assert _is_blocked(ipaddress.ip_address(endereco)), porque


@pytest.mark.parametrize(("endereco", "porque"), PERMITIR)
def test_a_public_address_is_not_blocked(endereco: str, porque: str) -> None:
    """The guard against fixing a blocklist by blocking everything.

    The two neighbours of the CGNAT range are the load-bearing cases: a mask written one bit wide
    swallows `100.128.0.0/9`, which is ordinary public space, and the tool would start refusing real
    sites with an error that says "internal address".
    """
    assert not _is_blocked(ipaddress.ip_address(endereco)), porque
