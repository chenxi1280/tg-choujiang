from dataclasses import dataclass
from hashlib import sha256

ALGORITHM_VERSION = "sha256-weighted-v1"


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
    digest = sha256(f"{seed}:{round_number}".encode()).digest()
    selected_value = int.from_bytes(digest, "big") % total + 1
    return _locate(valid_entries, selected_value)


def _locate(entries: tuple[WeightedEntry, ...], selected_value: int) -> Selection:
    cursor = 1
    for entry in entries:
        end = cursor + entry.weight - 1
        if selected_value <= end:
            return Selection(entry.participant_id, selected_value, cursor, end)
        cursor = end + 1
    raise AssertionError("selected value must be inside weighted range")
