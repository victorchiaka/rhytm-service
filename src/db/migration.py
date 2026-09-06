import datetime
import json

import aiofiles
from alembic import command, config
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from db.database import Base, get_db
from habits.models import Habit
from routines.models import Routine
from users.models import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

"""
This module is responsible for running database migrations and seeding the database with initial data.
"""


async def seed_users(db: AsyncSession, file_path: str):
    print(f"Seeding Users from {file_path}...")
    async with aiofiles.open(file_path, "r") as file:
        content = await file.read()
        users_data = json.loads(content)

    for data in users_data:
        result = await db.execute(select(User).where(User.email == data["email"]))
        existing = result.scalar_one_or_none()
        if existing is None:
            hashed_password = pwd_context.hash(data["password"])
            new_user = User(
                full_name=data["full_name"],
                email=data["email"],
                password=hashed_password,
                plan=data["plan"],
            )
            db.add(new_user)
    await db.commit()


async def seed_routines(db: AsyncSession, file_path: str):
    print(f"Seeding Routines from {file_path}...")
    async with aiofiles.open(file_path, "r") as file:
        content = await file.read()
        routines_data = json.loads(content)

    for data in routines_data:
        user_result = await db.execute(
            select(User).where(User.email == data["user_email"])
        )
        user = user_result.scalar_one_or_none()
        if not user:
            print(
                f"User {data['user_email']} not found, skipping routine '{data['name']}'"
            )
            continue

        result = await db.execute(
            select(Routine).where(
                Routine.name == data["name"], Routine.user_id == user.id
            )
        )
        existing = result.scalar_one_or_none()
        if existing is None:
            new_routine = Routine(
                user_id=user.id,
                name=data["name"],
                time_of_day=data["time_of_day"],
            )
            db.add(new_routine)
    await db.commit()


async def seed_habits(db: AsyncSession, file_path: str):
    print(f"Seeding Habits from {file_path}...")
    async with aiofiles.open(file_path, "r") as file:
        content = await file.read()
        habits_data = json.loads(content)

    for data in habits_data:
        user_result = await db.execute(
            select(User).where(User.email == data["user_email"])
        )
        user = user_result.scalar_one_or_none()
        if not user:
            print(
                f"User {data['user_email']} not found, skipping habit '{data['name']}'"
            )
            continue

        routine_result = await db.execute(
            select(Routine).where(
                Routine.name == data["routine_name"], Routine.user_id == user.id
            )
        )
        routine = routine_result.scalar_one_or_none()
        if not routine:
            print(
                f"Routine {data['routine_name']} not found for user, skipping habit '{data['name']}'"
            )
            continue

        result = await db.execute(
            select(Habit).where(Habit.name == data["name"], Habit.user_id == user.id)
        )
        existing = result.scalar_one_or_none()
        if existing is None:
            new_habit = Habit(
                user_id=user.id,
                routine_id=routine.id,
                name=data["name"],
                reminder_time=data.get("reminder_time"),
                days_of_week=data["days_of_week"],
            )
            db.add(new_habit)
    await db.commit()


async def run_seed_functions():
    """Runs all seed functions"""
    from db.database import engine

    # Ensure tables are created if not using Alembic purely
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async for db in get_db():
        await seed_users(db, "src/db/seed/users.json")
        await seed_routines(db, "src/db/seed/routines.json")
        await seed_habits(db, "src/db/seed/habits.json")
        break


def apply_migrations():
    """Runs Alembic Migrations."""
    # command.upgrade(config.Config("alembic.ini"), "head")
    # pass


def main():
    print("Running migrations...")
    apply_migrations()

    print("Running Seed...")
    import asyncio

    asyncio.run(run_seed_functions())
    print("Migrations & Seeding complete!")


if __name__ == "__main__":
    main()
