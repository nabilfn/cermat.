"""Create (or reset) a DEMO workspace for an existing account.

    docker compose exec api python -m scripts.seed_demo you@example.com [--reset]

Demo data only ever goes into a separate workspace flagged ``is_demo``; real
workspaces are never touched. The same action is available in the app via
"Load demo workspace".
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.core.security import normalise_email
from app.database import SessionLocal
from app.models import UserModel
from app.services.demo import create_demo_workspace, reset_demo_workspace


async def main(email: str, reset: bool) -> None:
    async with SessionLocal() as session:
        user = (
            await session.execute(select(UserModel).where(UserModel.email == normalise_email(email)))
        ).scalar_one_or_none()
        if user is None:
            raise SystemExit(f"No account for {email}. Sign up in the app first.")
        workspace = await create_demo_workspace(session, user.id)
        if reset:
            await reset_demo_workspace(session, workspace, user.id)
        await session.commit()
        print(f"Demo workspace ready: {workspace.name} ({workspace.id})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("email")
    parser.add_argument("--reset", action="store_true", help="Delete and re-create the demo records")
    args = parser.parse_args()
    asyncio.run(main(args.email, args.reset))
