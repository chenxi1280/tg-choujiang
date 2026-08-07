from enum import StrEnum

from sqlalchemy import Enum as SqlEnum


def enum_type(enum: type[StrEnum], name: str) -> SqlEnum:
    return SqlEnum(
        enum,
        name=name,
        native_enum=False,
        values_callable=lambda values: [item.value for item in values],
        validate_strings=True,
        create_constraint=True,
    )
