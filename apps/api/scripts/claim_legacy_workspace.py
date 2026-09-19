"""Give an account ownership of the data created before accounts existed (Phase 1–6).

    docker compose exec api python -m scripts.claim_legacy_workspace you@example.com

In development the first account created claims it automatically.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.core.security import normalise_email
from app.database import SessionLocal
from app.models import UserModel, WorkspaceMemberModel, WorkspaceModel


async def main(email: str) -> None:
    async with SessionLocal() as session:
        user = (
            await session.execute(select(UserModel).where(UserModel.email == normalise_email(email)))
        ).scalar_one_or_none()
        if user is None:
            raise SystemExit(f"No account for {email}.")
        legacy = (await session.execute(select(WorkspaceModel).where(WorkspaceModel.is_legacy.is_(True)))).scalars().first()
        if legacy is None:
            raise SystemExit("There is no legacy workspace to claim.")
        exists = await session.scalar(
            select(WorkspaceMemberModel.id).where(
                WorkspaceMemberModel.workspace_id == legacy.id, WorkspaceMemberModel.user_id == user.id
            )
        )
        if not exists:
            session.add(WorkspaceMemberModel(workspace_id=legacy.id, user_id=user.id, role="owner"))
            await session.commit()
        print(f"{email} owns '{legacy.name}'.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email")
    asyncio.run(main(parser.parse_args().email))
