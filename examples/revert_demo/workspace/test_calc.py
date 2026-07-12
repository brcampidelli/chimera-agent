"""The executable acceptance check — plain stdlib unittest, no extra deps."""

import unittest

from calc import add


class TestAdd(unittest.TestCase):
    def test_add(self) -> None:
        self.assertEqual(add(2, 3), 5)


if __name__ == "__main__":
    unittest.main()
