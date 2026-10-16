from __future__ import annotations

from enum import Enum as PyEnum

from sqlalchemy import Enum


def str_enum(enum_cls: type[PyEnum], name: str) -> Enum:
    """Enums stored as VARCHAR + CHECK: readable in SQL, cheap to extend via migration."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )
