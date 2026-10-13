"""Add price evidence, stable observations and immutable dataset kinds."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "price_datasets",
        sa.Column("dataset_id", sa.Text(), primary_key=True),
        sa.Column("data_kind", sa.Text(), nullable=False),
        sa.CheckConstraint("data_kind IN ('real', 'synthetic')"),
    )
    op.create_table(
        "price_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "dataset_id",
            sa.Text(),
            sa.ForeignKey("price_datasets.dataset_id"),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("price_basis", sa.Text(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_reference", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.Text(), nullable=False),
        sa.Column("metadata_sha256", sa.Text(), nullable=False),
        sa.Column("source_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("declarations", postgresql.JSONB(), nullable=False),
        sa.Column("result_counts", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("dataset_id", "source_sha256", "metadata_sha256"),
        sa.UniqueConstraint("id", "dataset_id"),
        sa.CheckConstraint("source_sha256 ~ '^[0-9a-f]{64}$'"),
        sa.CheckConstraint("metadata_sha256 ~ '^[0-9a-f]{64}$'"),
    )
    op.create_table(
        "price_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "dataset_id",
            sa.Text(),
            sa.ForeignKey("price_datasets.dataset_id"),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_symbol", sa.Text(), nullable=False),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column("price_basis", sa.Text(), nullable=False),
        sa.Column("normalized_values", postgresql.JSONB(), nullable=False),
        sa.Column("conflicted", sa.Boolean(), nullable=False),
        sa.Column("prices_usable", sa.Boolean(), nullable=False),
        sa.Column("volume_usable", sa.Boolean(), nullable=False),
        sa.UniqueConstraint(
            "dataset_id",
            "provider",
            "provider_symbol",
            "observation_date",
            "price_basis",
        ),
        sa.UniqueConstraint("id", "dataset_id"),
    )
    op.create_table(
        "price_rows",
        sa.Column("source_id", sa.Integer(), primary_key=True),
        sa.Column("record_position", sa.Integer(), primary_key=True),
        sa.Column("dataset_id", sa.Text(), nullable=False),
        sa.Column("observation_id", sa.Integer(), nullable=True),
        sa.Column("record_sha256", sa.Text(), nullable=False),
        sa.Column("original_fields", postgresql.JSONB(), nullable=False),
        sa.Column("normalized_values", postgresql.JSONB(), nullable=False),
        sa.Column("issues", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id", "dataset_id"],
            ["price_sources.id", "price_sources.dataset_id"],
        ),
        sa.ForeignKeyConstraint(
            ["observation_id", "dataset_id"],
            ["price_observations.id", "price_observations.dataset_id"],
        ),
        sa.CheckConstraint("record_position >= 0"),
        sa.CheckConstraint("record_sha256 ~ '^[0-9a-f]{64}$'"),
    )
    op.create_index("ix_price_rows_observation", "price_rows", ["observation_id"])


def downgrade() -> None:
    op.drop_index("ix_price_rows_observation", table_name="price_rows")
    op.drop_table("price_rows")
    op.drop_table("price_observations")
    op.drop_table("price_sources")
    op.drop_table("price_datasets")
