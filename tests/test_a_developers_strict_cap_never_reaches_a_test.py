"""A developer's own CHIMERA_STRICT_SPEND_CAP never reaches a test.

``SpendBudget()`` reads the owner's switch from the process environment when it is built, and
``patch_config`` exports what it saves there. A shell that inherited ``CHIMERA_STRICT_SPEND_CAP=true``
turned every test that builds ``SpendBudget(max_usd)`` without ``strict=`` red on that one machine.
``tests/conftest.py`` owns the name, the way it owns ``CHIMERA_REACH``. This test only bites when the
variable is set outside pytest, which is exactly the case it is for: run the suite with it exported
and this, not a dozen budget tests, says why.
"""

from __future__ import annotations

import os

from chimera.orchestration.budget import SpendBudget


def test_the_strict_switch_from_the_shell_is_not_seen_by_a_test() -> None:
    assert "CHIMERA_STRICT_SPEND_CAP" not in os.environ
    assert SpendBudget(max_usd=1.0).strict is False
