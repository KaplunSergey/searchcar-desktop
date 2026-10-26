"""Persist public search presets alongside native/legacy URLs."""
from alembic import op
import sqlalchemy as sa
from app.search_identity import url_identity_v1

revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('projects', sa.Column('search_config', sa.JSON(), nullable=True))
    op.add_column('projects', sa.Column('search_identity', sa.String(64), nullable=True))
    connection = op.get_bind()
    for row in connection.execute(sa.text('SELECT id, search_url FROM projects')):
        connection.execute(sa.text('UPDATE projects SET search_identity=:identity WHERE id=:id'),
                           {'id': row.id, 'identity': url_identity_v1(row.search_url)})
    with op.batch_alter_table('projects') as batch:
        batch.drop_constraint('uq_projects_owner_source_search_url', type_='unique')
        batch.create_unique_constraint('uq_projects_owner_source_search_identity', ['owner_id', 'source_key', 'search_identity'])
        batch.alter_column('search_identity', existing_type=sa.String(64), nullable=False)


def downgrade():
    raise RuntimeError('search_config_downgrade_requires_verified_backup')
