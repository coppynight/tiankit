import os
import time


def uuid_v7() -> str:
    """Generate a UUIDv7 string (RFC 9562)."""
    ts_ms = int(time.time() * 1000)
    ts_ms &= (1 << 48) - 1

    # 74 bits random (12 for rand_a, 62 for rand_b)
    rand74 = int.from_bytes(os.urandom(10), "big") >> 6
    rand_a = (rand74 >> 62) & ((1 << 12) - 1)
    rand_b = rand74 & ((1 << 62) - 1)

    value = (ts_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0x2 << 62) | rand_b
    hexstr = f"{value:032x}"
    return f"{hexstr[0:8]}-{hexstr[8:12]}-{hexstr[12:16]}-{hexstr[16:20]}-{hexstr[20:32]}"


def run_id(prefix: str = "r") -> str:
    return f"{prefix}-{uuid_v7()}"
