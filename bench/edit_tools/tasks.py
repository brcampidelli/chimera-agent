"""Twelve multi-file edit tasks — six families, two each.

Authored against `PREREGISTRATION.md`, which was committed first and fixes the rules these obey:

- **Every task touches >= 3 files.** A single-file task cannot discriminate between an arm with a
  multi-file batch edit and one without, so it would only pad n.
- **The verifier is pytest**, and the runner restores the test from a pristine copy before judging.
  Not byte-exact comparison against a reference: a rename has several correct spellings, and scoring
  only ours would measure obedience rather than capability.
- **Six families, two each**, fixed before authoring so the mix cannot be tuned after seeing which
  arm likes which shape.
- **None was authored by reading `ouroboros/tools/edit_ops.py`.** Copying their fixtures would
  measure how closely our tools resemble their bench.

The fixtures are deliberately small and boring. This bench measures *tool ergonomics* — how many
edit calls a change of a given shape costs — and says nothing about repository scale. A rename across
four toy modules is not a rename across django, and `PREREGISTRATION.md` registers that as a
limitation rather than discovering it later.
"""

from __future__ import annotations

from typing import Any

# --- family 1: rename a symbol across modules -----------------------------------------------------

_RENAME_A: dict[str, Any] = {
    "name": "rename_helper",
    "family": "rename",
    "min_files": 4,
    "prompt": (
        "Rename the function `calc_total` to `compute_total` everywhere in the `shop` package. "
        "Keep the behaviour identical. Do not change the tests."
    ),
    "test": "tests/test_shop.py",
    "files": {
        "shop/__init__.py": "from shop.cart import cart_total\nfrom shop.money import calc_total\n\n__all__ = ['calc_total', 'cart_total']\n",
        "shop/money.py": (
            "def calc_total(prices):\n"
            "    \"\"\"Sum a list of prices.\"\"\"\n"
            "    return sum(prices)\n"
        ),
        "shop/cart.py": (
            "from shop.money import calc_total\n\n\n"
            "def cart_total(items):\n"
            "    return calc_total([i['price'] for i in items])\n"
        ),
        "shop/report.py": (
            "from shop.money import calc_total\n\n\n"
            "def summary(prices):\n"
            "    return f'total={calc_total(prices)}'\n"
        ),
        "tests/test_shop.py": (
            "from shop.money import compute_total\n"
            "from shop.cart import cart_total\n"
            "from shop.report import summary\n"
            "import shop\n\n\n"
            "def test_renamed_everywhere():\n"
            "    assert compute_total([1, 2, 3]) == 6\n"
            "    assert cart_total([{'price': 2}, {'price': 5}]) == 7\n"
            "    assert summary([4]) == 'total=4'\n"
            "    assert not hasattr(shop.money, 'calc_total')\n"
        ),
    },
}

_RENAME_B: dict[str, Any] = {
    "name": "rename_class",
    "family": "rename",
    "min_files": 3,
    "prompt": (
        "Rename the class `Conn` to `Connection` everywhere in the `db` package, including its "
        "uses. Keep the behaviour identical. Do not change the tests."
    ),
    "test": "tests/test_db.py",
    "files": {
        "db/__init__.py": "from db.core import Conn\n\n__all__ = ['Conn']\n",
        "db/core.py": (
            "class Conn:\n"
            "    def __init__(self, dsn):\n"
            "        self.dsn = dsn\n\n"
            "    def describe(self):\n"
            "        return f'Conn({self.dsn})'\n"
        ),
        "db/pool.py": (
            "from db.core import Conn\n\n\n"
            "def make_pool(dsn, size):\n"
            "    return [Conn(dsn) for _ in range(size)]\n"
        ),
        "tests/test_db.py": (
            "from db.core import Connection\n"
            "from db.pool import make_pool\n"
            "import db.core\n\n\n"
            "def test_renamed():\n"
            "    assert Connection('x').describe() == 'Connection(x)'\n"
            "    assert len(make_pool('y', 3)) == 3\n"
            "    assert all(isinstance(c, Connection) for c in make_pool('y', 2))\n"
            "    assert not hasattr(db.core, 'Conn')\n"
        ),
    },
}

# --- family 2: change a signature and every caller -------------------------------------------------

_SIGNATURE_A: dict[str, Any] = {
    "name": "signature_swap_args",
    "family": "signature",
    "min_files": 3,
    "prompt": (
        "`format_name(first, last)` should take `(last, first)` instead — the argument ORDER "
        "changes, the output must not. Update every caller in the `people` package."
    ),
    "test": "tests/test_people.py",
    "files": {
        "people/__init__.py": "",
        "people/naming.py": (
            "def format_name(first, last):\n"
            "    return f'{last}, {first}'\n"
        ),
        "people/roster.py": (
            "from people.naming import format_name\n\n\n"
            "def roster(rows):\n"
            "    return [format_name(r['first'], r['last']) for r in rows]\n"
        ),
        "people/badge.py": (
            "from people.naming import format_name\n\n\n"
            "def badge(first, last):\n"
            "    return 'BADGE: ' + format_name(first, last)\n"
        ),
        "tests/test_people.py": (
            "import inspect\n"
            "from people.naming import format_name\n"
            "from people.roster import roster\n"
            "from people.badge import badge\n\n\n"
            "def test_order_changed_output_did_not():\n"
            "    assert list(inspect.signature(format_name).parameters) == ['last', 'first']\n"
            "    assert format_name('Souza', 'Ana') == 'Souza, Ana'\n"
            "    assert roster([{'first': 'Ana', 'last': 'Souza'}]) == ['Souza, Ana']\n"
            "    assert badge('Ana', 'Souza') == 'BADGE: Souza, Ana'\n"
        ),
    },
}

_SIGNATURE_B: dict[str, Any] = {
    "name": "signature_return_shape",
    "family": "signature",
    "min_files": 3,
    "prompt": (
        "`parse(line)` currently returns a tuple `(key, value)`. Make it return a dict "
        "`{'key': ..., 'value': ...}` and update every caller in the `cfg` package."
    ),
    "test": "tests/test_cfg.py",
    "files": {
        "cfg/__init__.py": "",
        "cfg/parser.py": (
            "def parse(line):\n"
            "    key, _, value = line.partition('=')\n"
            "    return key.strip(), value.strip()\n"
        ),
        "cfg/loader.py": (
            "from cfg.parser import parse\n\n\n"
            "def load(lines):\n"
            "    out = {}\n"
            "    for line in lines:\n"
            "        k, v = parse(line)\n"
            "        out[k] = v\n"
            "    return out\n"
        ),
        "cfg/audit.py": (
            "from cfg.parser import parse\n\n\n"
            "def keys(lines):\n"
            "    return [parse(line)[0] for line in lines]\n"
        ),
        "tests/test_cfg.py": (
            "from cfg.parser import parse\n"
            "from cfg.loader import load\n"
            "from cfg.audit import keys\n\n\n"
            "def test_dict_shape():\n"
            "    assert parse('a = 1') == {'key': 'a', 'value': '1'}\n"
            "    assert load(['a = 1', 'b = 2']) == {'a': '1', 'b': '2'}\n"
            "    assert keys(['a = 1', 'b = 2']) == ['a', 'b']\n"
        ),
    },
}

# --- family 3: move a constant and update its readers ----------------------------------------------

_CONSTANT_A: dict[str, Any] = {
    "name": "constant_move_module",
    "family": "constant",
    "min_files": 4,
    "prompt": (
        "`TIMEOUT` is defined in `net/client.py`. Move it to a new module `net/settings.py` "
        "and update everything that reads it. Its value must not change."
    ),
    "test": "tests/test_net.py",
    "files": {
        "net/__init__.py": "",
        "net/client.py": (
            "TIMEOUT = 30\n\n\n"
            "def fetch(url):\n"
            "    return f'{url} in {TIMEOUT}s'\n"
        ),
        "net/retry.py": (
            "from net.client import TIMEOUT\n\n\n"
            "def budget(attempts):\n"
            "    return TIMEOUT * attempts\n"
        ),
        "net/report.py": (
            "from net.client import TIMEOUT\n\n\n"
            "def line():\n"
            "    return f'timeout={TIMEOUT}'\n"
        ),
        "tests/test_net.py": (
            "from net.settings import TIMEOUT\n"
            "from net.client import fetch\n"
            "from net.retry import budget\n"
            "from net.report import line\n"
            "import net.client\n\n\n"
            "def test_moved():\n"
            "    assert TIMEOUT == 30\n"
            "    assert fetch('u') == 'u in 30s'\n"
            "    assert budget(3) == 90\n"
            "    assert line() == 'timeout=30'\n"
            "    assert 'TIMEOUT' not in vars(net.client)\n"
        ),
    },
}

_CONSTANT_B: dict[str, Any] = {
    "name": "constant_split_two",
    "family": "constant",
    "min_files": 3,
    "prompt": (
        "`LIMIT = 100` in `quota/rules.py` is used for two different things. Split it into "
        "`READ_LIMIT = 100` and `WRITE_LIMIT = 100`, and make `quota/reader.py` use the read one "
        "and `quota/writer.py` the write one."
    ),
    "test": "tests/test_quota.py",
    "files": {
        "quota/__init__.py": "",
        "quota/rules.py": "LIMIT = 100\n",
        "quota/reader.py": (
            "from quota.rules import LIMIT\n\n\n"
            "def allowed(n):\n"
            "    return n <= LIMIT\n"
        ),
        "quota/writer.py": (
            "from quota.rules import LIMIT\n\n\n"
            "def allowed(n):\n"
            "    return n <= LIMIT\n"
        ),
        "tests/test_quota.py": (
            "from quota.rules import READ_LIMIT, WRITE_LIMIT\n"
            "from quota import reader, writer\n"
            "import quota.rules\n\n\n"
            "def test_split():\n"
            "    assert READ_LIMIT == 100 and WRITE_LIMIT == 100\n"
            "    assert reader.allowed(100) and not reader.allowed(101)\n"
            "    assert writer.allowed(100) and not writer.allowed(101)\n"
            "    assert not hasattr(quota.rules, 'LIMIT')\n"
        ),
    },
}

# --- family 4: add a parameter with a default ------------------------------------------------------

_PARAM_A: dict[str, Any] = {
    "name": "param_add_default",
    "family": "param",
    "min_files": 3,
    "prompt": (
        "Give `slugify(text)` a second parameter `sep='-'` that replaces the separator. Existing "
        "callers in the `blog` package must keep working unchanged in behaviour."
    ),
    "test": "tests/test_blog.py",
    "files": {
        "blog/__init__.py": "",
        "blog/slug.py": (
            "def slugify(text):\n"
            "    return '-'.join(text.lower().split())\n"
        ),
        "blog/post.py": (
            "from blog.slug import slugify\n\n\n"
            "def url(title):\n"
            "    return '/p/' + slugify(title)\n"
        ),
        "blog/tag.py": (
            "from blog.slug import slugify\n\n\n"
            "def tag_url(name):\n"
            "    return '/t/' + slugify(name)\n"
        ),
        "tests/test_blog.py": (
            "from blog.slug import slugify\n"
            "from blog.post import url\n"
            "from blog.tag import tag_url\n\n\n"
            "def test_default_and_override():\n"
            "    assert slugify('Hello There') == 'hello-there'\n"
            "    assert slugify('Hello There', sep='_') == 'hello_there'\n"
            "    assert url('My Post') == '/p/my-post'\n"
            "    assert tag_url('Big Idea') == '/t/big-idea'\n"
        ),
    },
}

_PARAM_B: dict[str, Any] = {
    "name": "param_thread_through",
    "family": "param",
    "min_files": 3,
    "prompt": (
        "Add a `strict=False` parameter to `validate(rows)` in `forms/check.py`. When True it "
        "raises ValueError on an empty row instead of skipping it. Thread the parameter through "
        "`forms/intake.py` and `forms/batch.py` so a caller can ask for strict validation."
    ),
    "test": "tests/test_forms.py",
    "files": {
        "forms/__init__.py": "",
        "forms/check.py": (
            "def validate(rows):\n"
            "    return [r for r in rows if r]\n"
        ),
        "forms/intake.py": (
            "from forms.check import validate\n\n\n"
            "def intake(rows):\n"
            "    return validate(rows)\n"
        ),
        "forms/batch.py": (
            "from forms.check import validate\n\n\n"
            "def batch(groups):\n"
            "    return [validate(g) for g in groups]\n"
        ),
        "tests/test_forms.py": (
            "import pytest\n"
            "from forms.check import validate\n"
            "from forms.intake import intake\n"
            "from forms.batch import batch\n\n\n"
            "def test_threaded():\n"
            "    assert validate([{'a': 1}, {}]) == [{'a': 1}]\n"
            "    assert intake([{'a': 1}, {}]) == [{'a': 1}]\n"
            "    assert batch([[{'a': 1}, {}]]) == [[{'a': 1}]]\n"
            "    with pytest.raises(ValueError):\n"
            "        validate([{}], strict=True)\n"
            "    with pytest.raises(ValueError):\n"
            "        intake([{}], strict=True)\n"
            "    with pytest.raises(ValueError):\n"
            "        batch([[{}]], strict=True)\n"
        ),
    },
}

# --- family 5: correct a string repeated in N files ------------------------------------------------

_STRING_A: dict[str, Any] = {
    "name": "string_repeated_typo",
    "family": "string",
    "min_files": 3,
    "prompt": (
        "The word 'recieved' is misspelled in the `mail` package. It should be 'received'. "
        "Fix every occurrence."
    ),
    "test": "tests/test_mail.py",
    "files": {
        "mail/__init__.py": "",
        "mail/inbox.py": "def note():\n    return 'message recieved'\n",
        "mail/log.py": "def line(n):\n    return f'{n} recieved'\n",
        "mail/status.py": (
            "STATES = ['pending', 'recieved', 'archived']\n\n\n"
            "def label(i):\n"
            "    return STATES[i]\n"
        ),
        "tests/test_mail.py": (
            "from mail.inbox import note\n"
            "from mail.log import line\n"
            "from mail.status import STATES, label\n\n\n"
            "def test_spelling():\n"
            "    assert note() == 'message received'\n"
            "    assert line(3) == '3 received'\n"
            "    assert STATES == ['pending', 'received', 'archived']\n"
            "    assert label(1) == 'received'\n"
        ),
    },
}

_STRING_B: dict[str, Any] = {
    "name": "string_prefix_change",
    "family": "string",
    "min_files": 4,
    "prompt": (
        "Every log message in the `svc` package is prefixed with '[svc] '. Change the prefix to "
        "'[service] ' everywhere. Do not change anything else about the messages."
    ),
    "test": "tests/test_svc.py",
    "files": {
        "svc/__init__.py": "",
        "svc/start.py": "def msg():\n    return '[svc] starting'\n",
        "svc/stop.py": "def msg():\n    return '[svc] stopping'\n",
        "svc/health.py": "def msg(ok):\n    return '[svc] ok' if ok else '[svc] down'\n",
        "tests/test_svc.py": (
            "from svc.start import msg as start\n"
            "from svc.stop import msg as stop\n"
            "from svc.health import msg as health\n\n\n"
            "def test_prefix():\n"
            "    assert start() == '[service] starting'\n"
            "    assert stop() == '[service] stopping'\n"
            "    assert health(True) == '[service] ok'\n"
            "    assert health(False) == '[service] down'\n"
        ),
    },
}

# --- family 6: change an import path ---------------------------------------------------------------

_IMPORT_A: dict[str, Any] = {
    "name": "import_module_moved",
    "family": "import",
    "min_files": 4,
    "prompt": (
        "Move `util/text.py` to `util/strings/text.py` (a new subpackage) and update every import. "
        "The functions must keep working."
    ),
    "test": "tests/test_util.py",
    "files": {
        "util/__init__.py": "",
        "util/text.py": "def shout(s):\n    return s.upper() + '!'\n",
        "util/a.py": "from util.text import shout\n\n\ndef a(s):\n    return shout(s)\n",
        "util/b.py": "from util.text import shout\n\n\ndef b(s):\n    return shout(s) * 2\n",
        "tests/test_util.py": (
            "import importlib\n"
            "from util.strings.text import shout\n"
            "from util.a import a\n"
            "from util.b import b\n\n\n"
            "def test_moved():\n"
            "    assert shout('hi') == 'HI!'\n"
            "    assert a('hi') == 'HI!'\n"
            "    assert b('hi') == 'HI!HI!'\n"
            "    try:\n"
            "        importlib.import_module('util.text')\n"
            "    except ModuleNotFoundError:\n"
            "        return\n"
            "    raise AssertionError('util.text should be gone')\n"
        ),
    },
}

_IMPORT_B: dict[str, Any] = {
    "name": "import_relative_to_absolute",
    "family": "import",
    "min_files": 3,
    "prompt": (
        "The `api` package uses relative imports (`from .x import y`). Convert every one of them "
        "to absolute imports (`from api.x import y`). Behaviour must not change."
    ),
    "test": "tests/test_api_pkg.py",
    "files": {
        "api/__init__.py": "",
        "api/base.py": "def ping():\n    return 'pong'\n",
        "api/v1.py": "from .base import ping\n\n\ndef handler():\n    return ping()\n",
        "api/v2.py": "from .base import ping\nfrom .v1 import handler\n\n\ndef both():\n    return ping() + handler()\n",
        "tests/test_api_pkg.py": (
            "from pathlib import Path\n"
            "from api.v1 import handler\n"
            "from api.v2 import both\n\n\n"
            "def test_absolute_imports():\n"
            "    assert handler() == 'pong'\n"
            "    assert both() == 'pongpong'\n"
            "    for name in ('v1.py', 'v2.py'):\n"
            "        src = Path('api', name).read_text(encoding='utf-8')\n"
            "        assert 'from .' not in src, f'{name} still has a relative import'\n"
            "        assert 'from api.' in src\n"
        ),
    },
}

#: All twelve, in family order. The pilot takes the FIRST of each family (see `run_pilot.py`), so the
#: six it does not run stay unseen until stage 2 — a task cannot be swapped in after watching how its
#: sibling behaved.
TASKS: list[dict[str, Any]] = [
    _RENAME_A, _RENAME_B,
    _SIGNATURE_A, _SIGNATURE_B,
    _CONSTANT_A, _CONSTANT_B,
    _PARAM_A, _PARAM_B,
    _STRING_A, _STRING_B,
    _IMPORT_A, _IMPORT_B,
]

FAMILIES = ["rename", "signature", "constant", "param", "string", "import"]

#: The pilot's six: one per family, the first of each pair.
PILOT: list[dict[str, Any]] = [next(t for t in TASKS if t["family"] == f) for f in FAMILIES]
