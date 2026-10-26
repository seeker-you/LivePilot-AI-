import unittest

from app.security import create_token, decode_token, hash_password, verify_password


class JWTTest(unittest.TestCase):
    def test_roundtrip(self):
        token = create_token({"sub": "1", "role": "admin"}, 60)
        claims = decode_token(token)
        self.assertEqual(claims["sub"], "1")
        self.assertEqual(claims["role"], "admin")

    def test_expired_token(self):
        token = create_token({"sub": "1"}, -10)
        self.assertIsNone(decode_token(token))

    def test_tampered_token(self):
        token = create_token({"sub": "1"}, 60)
        self.assertIsNone(decode_token(token + "x"))


class PasswordTest(unittest.TestCase):
    def test_hash_and_verify(self):
        digest, salt = hash_password("secret123")
        self.assertTrue(verify_password("secret123", salt, digest))
        self.assertFalse(verify_password("wrong", salt, digest))


if __name__ == "__main__":
    unittest.main()

