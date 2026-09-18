from fastapi import APIRouter

habits_router = APIRouter(prefix="/habits", tags=["Habits"])

habit_service = HabitService()
