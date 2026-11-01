"""Password hashing with scrypt from the standard library, one-time passwords, and the password rule (which lives in rules.py and its settings file)."""
import hashlib
import hmac
import secrets

from welding_ai.accounts import rules

N, R, P = 2 ** 14, 8, 1
MAXMEM = 64 * 1024 * 1024
ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # no look-alike characters (0/O, 1/l/I)


# "scrypt$N$r$p$salt$hash": the cost settings travel with the hash, so they can be raised later.
def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, maxmem=MAXMEM)
    return f"scrypt${N}${R}${P}${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        probe = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), maxmem=MAXMEM)
        return hmac.compare_digest(probe, bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False


# A readable one-time password such as "kT7m-Qp3x-Hn9w". It always meets the password rule (an uppercase letter, a number and the dashes as symbols).
def temp_password():
    chars = [secrets.choice(ALPHABET) for _ in range(12)]
    chars[0] = secrets.choice("ABCDEFGHJKMNPQRSTUVWXYZ")
    chars[1] = secrets.choice("23456789")
    secrets.SystemRandom().shuffle(chars)
    return "-".join("".join(chars[i:i + 4]) for i in range(0, 12, 4))


# The reason a new password is not acceptable (the message from the settings file), or None.
def password_problem(password, username=""):
    return rules.current().password_problem(password, username)
