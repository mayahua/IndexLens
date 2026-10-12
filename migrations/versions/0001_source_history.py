"""source_history"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("dataset_sha256", sa.Text(), nullable=False),
        sa.Column("dataset_kind", sa.Text(), nullable=False),
        sa.Column("coverage_start", sa.Date(), nullable=False),
        sa.Column("coverage_end", sa.Date(), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column(
            "source_columns", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("source_bytes", sa.LargeBinary(), nullable=False),
        sa.Column(
            "frame_dtypes", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.CheckConstraint("dataset_kind IN ('real', 'synthetic')"),
        sa.CheckConstraint("dataset_sha256 ~ '^[0-9a-f]{64}$'"),
        sa.CheckConstraint("coverage_start <= coverage_end"),
        sa.CheckConstraint("record_count > 0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "dataset_sha256", "dataset_kind"),
    )
    op.create_table(
        "source_identities",
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("identity_position", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("cik", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["source_versions.id"],
        ),
        sa.PrimaryKeyConstraint("version_id", "identity_position"),
        sa.UniqueConstraint("version_id", "symbol", "cik"),
    )
    op.create_table(
        "source_issues",
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("issue_position", sa.Integer(), nullable=False),
        sa.Column("issue_type", sa.Text(), nullable=False),
        sa.Column("issue_details", sa.Text(), nullable=False),
        sa.Column("column", sa.Text(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["source_versions.id"],
        ),
        sa.PrimaryKeyConstraint("version_id", "issue_position"),
    )
    op.create_table(
        "source_rows",
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("source_record_position", sa.Integer(), nullable=False),
        sa.Column("identity_position", sa.Integer(), nullable=False),
        sa.Column("record_sha256", sa.Text(), nullable=False),
        sa.Column(
            "original_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "audited_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.CheckConstraint("record_sha256 ~ '^[0-9a-f]{64}$'"),
        sa.CheckConstraint("source_record_position >= 0"),
        sa.ForeignKeyConstraint(
            ["version_id", "identity_position"],
            ["source_identities.version_id", "source_identities.identity_position"],
        ),
        sa.PrimaryKeyConstraint("version_id", "source_record_position"),
    )
    op.create_index(
        "ix_source_rows_identity",
        "source_rows",
        ["version_id", "identity_position"],
        unique=False,
    )
    op.create_table(
        "membership_intervals",
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("source_record_position", sa.Integer(), nullable=False),
        sa.Column("date_added", sa.Date(), nullable=False),
        sa.Column("date_removed", sa.Date(), nullable=True),
        sa.Column("created_at", sa.Date(), nullable=False),
        sa.Column("date_added_approximate", sa.Boolean(), nullable=False),
        sa.Column("date_removed_approximate", sa.Boolean(), nullable=False),
        sa.CheckConstraint("date_removed IS NULL OR date_removed >= date_added"),
        sa.ForeignKeyConstraint(
            ["version_id", "source_record_position"],
            ["source_rows.version_id", "source_rows.source_record_position"],
        ),
        sa.PrimaryKeyConstraint("version_id", "source_record_position"),
    )
    op.create_index(
        "ix_membership_intervals_date",
        "membership_intervals",
        ["version_id", "date_added"],
        unique=False,
    )
    op.create_table(
        "source_events",
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("source_record_position", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("event_date_approximate", sa.Boolean(), nullable=False),
        sa.Column("created_after_event", sa.Boolean(), nullable=False),
        sa.CheckConstraint("event_type IN ('ADD', 'REMOVE')"),
        sa.ForeignKeyConstraint(
            ["version_id", "source_record_position"],
            [
                "membership_intervals.version_id",
                "membership_intervals.source_record_position",
            ],
        ),
        sa.PrimaryKeyConstraint("version_id", "source_record_position", "event_type"),
    )
    op.create_index(
        "ix_source_events_date",
        "source_events",
        ["version_id", "event_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_source_events_date", table_name="source_events")
    op.drop_table("source_events")
    op.drop_index("ix_membership_intervals_date", table_name="membership_intervals")
    op.drop_table("membership_intervals")
    op.drop_index("ix_source_rows_identity", table_name="source_rows")
    op.drop_table("source_rows")
    op.drop_table("source_issues")
    op.drop_table("source_identities")
    op.drop_table("source_versions")
