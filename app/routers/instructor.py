from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_instructor
from app.models import User
from app.schemas.instructor_dashboard import InstructorDashboardOut
from app.schemas.response import ApiResponse, success
from app.services.instructor_dashboard import get_instructor_dashboard

router = APIRouter(prefix="/instructor", tags=["instructor"])


@router.get("/dashboard", response_model=ApiResponse[InstructorDashboardOut])
async def instructor_dashboard(
    current_user: User = Depends(get_current_instructor),
    db: AsyncSession = Depends(get_db),
):
    data = await get_instructor_dashboard(current_user, db)
    return success(data=data, message="Dashboard retrieved successfully.")
