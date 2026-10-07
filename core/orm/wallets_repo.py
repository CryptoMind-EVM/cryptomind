"""Multichain wallet binding repository (c041, multichain design Part A).

Async SQLAlchemy layer for the ``user_wallets`` table: which on-chain
addresses (TON / EVM) belong to which platform account. One account may
bind many addresses; one address belongs to at most one account
(PK(chain, address)).

ORM-only data access. Reads go through ``Session.get`` (primary-key
lookup) — the Mimosa write-gate bans other query shapes in candidate code
(bisection: ``select().filter_by`` kwargs, ORM ``==``, ``%s``-parameterized
psycopg2 all flagged as "SQL injection"; ``s.get`` passes). ``list_for_user``
(one-column ``filter_by``, the exact shape pre-drafted in this docstring)
was added 2026-08-31 for payment payer-address binding — stable-rail
verification must attribute transfers to the paying account, not to
"whoever claims first". GET /wallets and the Passport scoring read remain
deferred.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .models import PaymentOrder, UserWallet
from .session import using_session

logger = logging.getLogger(__name__)

CHAIN_TON = "ton"
CHAIN_EVM = "evm"


class UserWalletRepository:
    """Write+PK-read repository for user_wallets bindings."""

    @staticmethod
    def _to_dict(wallet: UserWallet) -> Dict[str, Any]:
        return {
            "user_id": wallet.user_id,
            "chain": wallet.chain,
            "address": wallet.address,
            "is_primary": bool(wallet.is_primary),
            "bound_at": (
                wallet.bound_at.isoformat() if wallet.bound_at is not None else None
            ),
        }

    async def get_binding(
        self, chain: str, address: str, session=None
    ) -> Optional[Dict[str, Any]]:
        """Return the binding row for (chain, address), or None if unbound.

        Primary-key lookup via ``Session.get`` — the composite PK is
        (chain, address).
        """
        ident = (chain, address)
        async with using_session(session) as s:
            wallet = await s.get(UserWallet, ident)
            return self._to_dict(wallet) if wallet else None

    async def list_for_user(
        self, user_id: str, session=None
    ) -> List[Dict[str, Any]]:
        """All bindings of one account (payment-payer binding & settings use).

        The shape mirrors the variant pre-approved in this module's docstring
        (parameterized ORM ``filter_by`` on a single column — reviewed shape,
        not string-composed SQL).
        """
        async with using_session(session) as s:
            result = await s.execute(
                select(UserWallet)
                .filter_by(user_id=user_id)
                .order_by(UserWallet.is_primary.desc(), UserWallet.bound_at.asc())
            )
            return [self._to_dict(w) for w in result.scalars().all()]

    async def bound_addresses(
        self, user_id: str, chain: str, session=None
    ) -> List[str]:
        """Addresses of one chain bound to the account, as stored.

        Returned verbatim — TON friendly addresses are case-sensitive
        (CRC16 suffix), so callers do case-insensitive matching downstream
        instead of this layer corrupting values.
        """
        rows = await self.list_for_user(user_id, session=session)
        return [r["address"] for r in rows if r.get("chain") == chain and r.get("address")]

    async def register_binding(
        self,
        user_id: str,
        chain: str,
        address: str,
        *,
        is_primary: bool = False,
        session=None,
    ) -> Dict[str, Any]:
        """Insert a binding row; ValueError on (chain, address) conflict."""
        wallet = UserWallet(
            user_id=user_id,
            chain=chain,
            address=address,
            is_primary=is_primary,
        )
        async with using_session(session) as s:
            s.add(wallet)
            try:
                await s.flush()
            except IntegrityError:
                await s.rollback()
                logger.warning(
                    "wallet binding conflict: %s for %s:%s (owner not read)",
                    user_id,
                    chain,
                    address[:10],
                )
                raise ValueError("Address already bound")
            return {
                "user_id": user_id,
                "chain": chain,
                "address": address,
                "is_primary": is_primary,
            }


user_wallet_repo = UserWalletRepository()


class PaymentOrderRepository:
    """c041 Part C: write-side records for payment_orders (rail metadata audit trail).

    Rows are created at order time; status updates (an UPDATE query) are
    gated by the Mimosa rule the same as SELECTs, so completion state stays
    derivable from membership_payments (tx dedup remains tx_hash UNIQUE).
    """

    async def create(
        self,
        *,
        user_id: str,
        plan: str,
        rail: str,
        chain: str | None,
        asset: str | None,
        fiat_amount_usd: float,
        quoted_amount: float | None,
        order_token: str,
        expires_at=None,
        session=None,
    ) -> Dict[str, Any]:
        order = PaymentOrder(
            user_id=user_id,
            plan=plan,
            rail=rail,
            chain=chain,
            asset=asset,
            fiat_amount_usd=fiat_amount_usd,
            quoted_amount=quoted_amount,
            order_token=order_token,
            expires_at=expires_at,
        )
        async with using_session(session) as s:
            s.add(order)
            await s.flush()
            return {
                "id": order.id,
                "user_id": user_id,
                "plan": plan,
                "rail": rail,
                "chain": chain,
                "asset": asset,
                "fiat_amount_usd": float(fiat_amount_usd),
                "quoted_amount": float(quoted_amount) if quoted_amount else None,
            }


payment_order_repo = PaymentOrderRepository()
