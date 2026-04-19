"""initial_schema

Revision ID: 373326eef00d
Revises: 
Create Date: 2026-04-19 11:10:32.493643

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '373326eef00d'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all tables from scratch."""

    # ── users ──
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=50), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('full_name', sa.String(length=100), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True, default=True),
        sa.Column('is_onboarded', sa.Boolean(), nullable=True, default=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('username'),
        sa.UniqueConstraint('email'),
    )
    op.create_index(op.f('ix_users_id'), 'users', ['id'], unique=False)

    # ── user_settings ──
    op.create_table(
        'user_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('kite_api_key', sa.String(length=255), nullable=True),
        sa.Column('kite_api_secret', sa.Text(), nullable=True),
        sa.Column('kite_redirect_url', sa.String(length=500), nullable=True, default=''),
        sa.Column('trading_enabled', sa.Boolean(), nullable=True, default=False),
        sa.Column('paper_trade', sa.Boolean(), nullable=True, default=True),
        sa.Column('max_loss_per_day', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('max_trades_per_day', sa.Integer(), nullable=True),
        sa.Column('max_position_size', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('max_single_order_value', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('active_strategies', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id'),
    )
    op.create_index(op.f('ix_user_settings_id'), 'user_settings', ['id'], unique=False)

    # ── zerodha_sessions ──
    op.create_table(
        'zerodha_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('access_token', sa.Text(), nullable=False),
        sa.Column('login_date', sa.Date(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'login_date'),
    )
    op.create_index(op.f('ix_zerodha_sessions_id'), 'zerodha_sessions', ['id'], unique=False)

    # ── strategy_configs ──
    op.create_table(
        'strategy_configs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('strategy_name', sa.String(length=100), nullable=False),
        sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'strategy_name'),
    )
    op.create_index(op.f('ix_strategy_configs_id'), 'strategy_configs', ['id'], unique=False)

    # ── strategy_states ──
    op.create_table(
        'strategy_states',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('strategy_name', sa.String(length=100), nullable=False),
        sa.Column('state', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('trading_date', sa.Date(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'strategy_name'),
    )
    op.create_index(op.f('ix_strategy_states_id'), 'strategy_states', ['id'], unique=False)

    # ── trade_logs ──
    op.create_table(
        'trade_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('strategy_name', sa.String(length=100), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('signal', sa.String(length=10), nullable=True),
        sa.Column('option_symbol', sa.String(length=100), nullable=True),
        sa.Column('atm_strike', sa.Integer(), nullable=True),
        sa.Column('entry_price', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('exit_price', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('exit_type', sa.String(length=50), nullable=True),
        sa.Column('exit_time', sa.String(length=20), nullable=True),
        sa.Column('lot_size', sa.Integer(), nullable=True),
        sa.Column('pnl', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('extra', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_trade_logs_id'), 'trade_logs', ['id'], unique=False)
    op.create_index('idx_trade_logs_user_date', 'trade_logs', ['user_id', 'trade_date'], unique=False)

    # ── order_history ──
    op.create_table(
        'order_history',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('order_date', sa.Date(), nullable=False),
        sa.Column('order_time', sa.DateTime(), nullable=True),
        sa.Column('tradingsymbol', sa.String(length=100), nullable=False),
        sa.Column('exchange', sa.String(length=10), nullable=True),
        sa.Column('transaction_type', sa.String(length=10), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('price', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('average_price', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('status', sa.String(length=30), nullable=True),
        sa.Column('order_id', sa.String(length=50), nullable=True),
        sa.Column('tag', sa.String(length=50), nullable=True),
        sa.Column('order_type', sa.String(length=20), nullable=True),
        sa.Column('product', sa.String(length=10), nullable=True),
        sa.Column('extra', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_order_history_id'), 'order_history', ['id'], unique=False)
    op.create_index('idx_order_history_user_date', 'order_history', ['user_id', 'order_date'], unique=False)


def downgrade() -> None:
    """Drop all tables."""
    op.drop_table('order_history')
    op.drop_table('trade_logs')
    op.drop_table('strategy_states')
    op.drop_table('strategy_configs')
    op.drop_table('zerodha_sessions')
    op.drop_table('user_settings')
    op.drop_table('users')
