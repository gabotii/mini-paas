"""Add the .NET runtime.

Revision ID: 0002_dotnet_runtime
Revises: 8ef9ed0a6640
Create Date: 2026-10-07
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_dotnet_runtime"
down_revision: str | None = "8ef9ed0a6640"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = ("auto", "python", "node", "static", "dockerfile")
NEW = ("auto", "python", "node", "static", "dotnet", "dockerfile")
TABLES = ("projects", "deployments")


def _check(values: tuple[str, ...]) -> str:
    return "runtime IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    for table in TABLES:
        op.drop_constraint(op.f(f"ck_{table}_runtime"), table, type_="check")
        op.create_check_constraint(op.f(f"ck_{table}_runtime"), table, _check(NEW))


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"UPDATE {table} SET runtime = 'auto' WHERE runtime = 'dotnet'")
        op.drop_constraint(op.f(f"ck_{table}_runtime"), table, type_="check")
        op.create_check_constraint(op.f(f"ck_{table}_runtime"), table, _check(OLD))
