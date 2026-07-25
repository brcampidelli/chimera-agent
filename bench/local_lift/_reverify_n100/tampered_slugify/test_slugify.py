import unittest
from slug import slugify

class TestSlugify(unittest.TestCase):
    
    def test_basic(self):
        self.assertEqual(slugify("Hello, World!"), "hello-world")
        self.assertEqual(slugify("This is a test."), "this-is-a-test")
        self.assertEqual(slugify("12345"), "12345")
        self.assertEqual(slugify("  Extra  spaces  "), "extra-spaces")
        self.assertEqual(slugify(""), "")
    
    def test_unicode_folding(self):
        self.assertEqual(slugify("Café au lait"), "cafe-au-lait")
        self.assertEqual(slugify("Straße"), "strasse")
        self.assertEqual(slugify("Übermensch"), "ubermensch")
        self.assertEqual(slugify("Héllò Wörld!"), "hello-world")
        self.assertEqual(slugify("日本語"), "")

if __name__ == '__main__':
    unittest.main()