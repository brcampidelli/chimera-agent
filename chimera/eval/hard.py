"""Hard benchmark suites — the ones that actually expose EvoClaw degradation.

``demo_tasks`` / ``demo_chain`` are trivial (they ceiling out at 100%, so single-model
and fusion look identical). These are the harder suites:

- :func:`hard_tasks` — 12 reasoning traps where even a strong single model slips.
- :func:`hard_chain` — an 8-step **stateful** arithmetic chain. Each step transforms a
  running number, so a single mistake **propagates**: the corrupted state is carried into
  every later step, which then also fails. That is the error-propagation + long-horizon
  failure mode Chimera is built to resist, and observed live: a single model breaks on the
  digit-sum step and collapses from 100% to 0% in the second half, while fusion holds.

Everything here is deterministic (the checks are pure and the chain's correct sequence is
fixed), so it unit-tests without a model.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from chimera.eval.chained import ChainStep
from chimera.eval.continuous import EvalTask

_ONLY = " Reply with ONLY the number, nothing else."


def _ints(text: str) -> list[str]:
    return re.findall(r"-?\d+", text.replace(",", ""))


def _has_num(expected: int) -> Callable[[str], bool]:
    return lambda out: str(expected) in _ints(out)


def _has_word(word: str) -> Callable[[str], bool]:
    return lambda out: word.lower() in out.lower()


def hard_tasks() -> list[EvalTask]:
    """Twelve reasoning traps with deterministic checks."""
    return [
        EvalTask("bat_ball", "A bat and a ball cost $1.10 total. The bat costs $1.00 more than the ball. How many CENTS does the ball cost?" + _ONLY, _has_num(5)),
        EvalTask("widgets", "5 machines take 5 minutes to make 5 widgets. How many minutes for 100 machines to make 100 widgets?" + _ONLY, _has_num(5)),
        EvalTask("letter_r", "How many times does the letter 'r' appear in the word 'strawberry'?" + _ONLY, _has_num(3)),
        EvalTask("lily", "A lily patch doubles in size every day and covers a lake in 48 days. On which day was the lake half covered?" + _ONLY, _has_num(47)),
        EvalTask("killers", "There are 3 killers in a room. A person enters and kills one of them. Nobody leaves. How many killers are in the room now?" + _ONLY, _has_num(3)),
        EvalTask("look_say", "Look-and-say sequence: 1, 11, 21, 1211, 111221, ... What is the NEXT term?" + _ONLY, _has_num(312211)),
        EvalTask("sister_age", "When I was 6, my sister was half my age. Now I am 70. How old is my sister?" + _ONLY, _has_num(67)),
        EvalTask("socks", "A drawer has 10 red and 10 blue socks, mixed, in complete darkness. How many socks must you pull to be CERTAIN you have a matching pair?" + _ONLY, _has_num(3)),
        EvalTask("towels", "1 towel takes 1 hour to dry in the sun. Laid out side by side, how many hours do 3 towels take to dry?" + _ONLY, _has_num(1)),
        EvalTask("apples", "I have 3 apples and eat 2 pears. How many apples do I have left?" + _ONLY, _has_num(3)),
        EvalTask("days", "If today is Monday, what day of the week is it 100 days from now? Reply with ONLY the weekday name.", _has_word("wednesday")),
        EvalTask("feathers", "Which weighs more: one kilogram of feathers or one kilogram of bricks? Reply with ONLY one word: same, feathers, or bricks.", _has_word("same")),
    ]


def hard_tasks_plus() -> list[EvalTask]:
    """The 12 traps of :func:`hard_tasks` plus 12 more — a bigger n for a tighter paired CI.

    Kept separate from :func:`hard_tasks` so other benches/tests that expect the 12-task suite are
    unaffected; used by ``skillcard-bench --tasks big`` for the M19-A1 accuracy-power A/B.
    """
    return hard_tasks() + [
        EvalTask("clock_strikes", "A clock takes 5 seconds to strike 6 o'clock (6 strikes). How many seconds does it take to strike 12 o'clock (12 strikes)?" + _ONLY, _has_num(11)),
        EvalTask("pills_30", "A doctor gives you 3 pills and says to take one every 30 minutes. How many minutes until you have taken all of them?" + _ONLY, _has_num(60)),
        EvalTask("sheep_but_9", "A farmer has 17 sheep. All but 9 die. How many sheep are still alive?" + _ONLY, _has_num(9)),
        EvalTask("overtake_2nd", "In a running race you just overtook the runner who was in 2nd place. What place are you in now?" + _ONLY, _has_num(2)),
        EvalTask("hens_eggs", "If 3 hens lay 3 eggs in 3 days, how many eggs do 6 hens lay in 6 days?" + _ONLY, _has_num(12)),
        EvalTask("banana_letters", "How many letters are in the word 'banana'?" + _ONLY, _has_num(6)),
        EvalTask("mississippi_s", "How many times does the letter 's' appear in the word 'mississippi'?" + _ONLY, _has_num(4)),
        EvalTask("months_28", "How many months of the year have exactly 28 days?" + _ONLY, _has_num(12)),
        EvalTask("candles", "You have 5 candles and blow 2 of them out right away. The rest burn all the way down. How many candles remain in the end?" + _ONLY, _has_num(2)),
        EvalTask("coin_next", "A fair coin has landed on heads 5 times in a row. What is the percent chance the next flip is heads?" + _ONLY, _has_num(50)),
        EvalTask("pages_read", "You read a book from the start of page 20 to the end of page 30, inclusive. How many pages did you read?" + _ONLY, _has_num(11)),
        EvalTask("half_of_half", "What is half of half of 100?" + _ONLY, _has_num(25)),
    ]


HARD_CHAIN_START = "17"

# (id, instruction template, correct operation) — the state is always the first integer
# in the rendered prompt, which keeps the chain deterministically checkable.
HARD_CHAIN_OPS: list[tuple[str, str, Callable[[int], int]]] = [
    ("mul4", "The number is {s}. Multiply it by 4.", lambda n: n * 4),
    ("sub9", "The number is {s}. Subtract 9 from it.", lambda n: n - 9),
    ("rev1", "The number is {s}. Reverse its digits (e.g. 59 -> 95).", lambda n: int(str(n)[::-1])),
    ("add128", "The number is {s}. Add 128 to it.", lambda n: n + 128),
    ("digitx11", "The number is {s}. Take the sum of its digits, then multiply that sum by 11.", lambda n: sum(int(d) for d in str(abs(n))) * 11),
    ("sub19", "The number is {s}. Subtract 19 from it.", lambda n: n - 19),
    ("rev2", "The number is {s}. Reverse its digits (e.g. 58 -> 85).", lambda n: int(str(abs(n))[::-1]) * (-1 if n < 0 else 1)),
    ("add100", "The number is {s}. Add 100 to it.", lambda n: n + 100),
]


def hard_chain() -> list[ChainStep]:
    """The 8-step stateful chain (start :data:`HARD_CHAIN_START`)."""
    running = int(HARD_CHAIN_START)
    steps: list[ChainStep] = []
    for name, template, op in HARD_CHAIN_OPS:
        running = op(running)
        steps.append(
            ChainStep(
                id=name,
                render=(lambda tmpl: lambda s: tmpl.format(s=s) + _ONLY)(template),
                integrate=lambda s, out: (_ints(out)[0] if _ints(out) else s),
                check=(lambda expected: lambda s: s.strip() == str(expected))(running),
            )
        )
    return steps
