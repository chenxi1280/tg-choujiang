from dataclasses import dataclass
from hashlib import sha256

ALGORITHM_VERSION = "sha256-weighted-v2"
_HASH_SPACE = 1 << 256


@dataclass(frozen=True)
class WeightedEntry:
    participant_id: str
    weight: int


@dataclass(frozen=True)
class Selection:
    participant_id: str
    selected_value: int
    range_start: int
    range_end: int


def select_weighted(entries: tuple[WeightedEntry, ...], seed: str, round_number: int) -> Selection:
    valid_entries = tuple(entry for entry in entries if entry.weight > 0)
    total = sum(entry.weight for entry in valid_entries)
    if total <= 0:
        raise ValueError("weighted draw requires at least one positive weight")
    selected_value = _unbiased_value(seed, round_number, total)
    return _locate(valid_entries, selected_value)


def _unbiased_value(seed: str, round_number: int, total: int) -> int:
    limit = _HASH_SPACE - (_HASH_SPACE % total)
    nonce = 0
    while True:
        digest = sha256(f"{seed}:{round_number}:{nonce}".encode()).digest()
        value = int.from_bytes(digest, "big")
        if value < limit:
            return value % total + 1
        nonce += 1


def _locate(entries: tuple[WeightedEntry, ...], selected_value: int) -> Selection:
    cursor = 1
    for entry in entries:
        end = cursor + entry.weight - 1
        if selected_value <= end:
            return Selection(entry.participant_id, selected_value, cursor, end)
        cursor = end + 1
    raise AssertionError("selected value must be inside weighted range")
