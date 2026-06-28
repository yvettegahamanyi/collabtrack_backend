import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.schemas.report import MeetingInputMeta
from app.schemas.response import ApiResponse, success
from app.schemas.training import TrainingCollectionDetailOut, TrainingCollectionOut
from app.services.report_creation import parse_url_list
from app.services.training_engine import (
    collect_training_data,
    get_training_collection_detail,
    list_training_collections,
)

router = APIRouter(prefix="/training", tags=["training"])


def _parse_meetings_meta(raw: str | None) -> list[MeetingInputMeta]:
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="meetings_meta must be valid JSON.",
        ) from exc
    if not isinstance(payload, list):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="meetings_meta must be a JSON array.",
        )
    return [MeetingInputMeta.model_validate(item) for item in payload]


@router.post(
    "/collections",
    response_model=ApiResponse[TrainingCollectionDetailOut],
    status_code=status.HTTP_201_CREATED,
    summary="Collect training data for one group",
)
async def create_training_collection(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    form = await request.form()
    identity_csv = form.get("identity_csv")
    if not isinstance(identity_csv, UploadFile):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="identity_csv is required.",
        )

    github_list_raw = form.get("github_urls")
    google_list_raw = form.get("google_doc_urls")
    github_single = form.get("github_url")
    google_single = form.get("google_doc_url")
    meetings_raw = form.get("meetings_meta")
    meetings_meta = _parse_meetings_meta(
        meetings_raw if isinstance(meetings_raw, str) else None
    )

    github_urls = parse_url_list(
        github_list_raw if isinstance(github_list_raw, str) else None
    )
    google_doc_urls = parse_url_list(
        google_list_raw if isinstance(google_list_raw, str) else None
    )
    if isinstance(github_single, str) and github_single.strip():
        github_urls.append(github_single.strip())
    if isinstance(google_single, str) and google_single.strip():
        google_doc_urls.append(google_single.strip())
    github_urls = list(dict.fromkeys(github_urls))
    google_doc_urls = list(dict.fromkeys(google_doc_urls))

    meeting_files: list[tuple[UploadFile, UploadFile, UploadFile | None]] = []
    for index in range(len(meetings_meta)):
        att = form.get(f"meetings_{index}_attendance")
        trans = form.get(f"meetings_{index}_transcript")
        chat = form.get(f"meetings_{index}_chat")
        if not isinstance(att, UploadFile) or not isinstance(trans, UploadFile):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Meeting {index + 1} requires attendance and transcript files."
                ),
            )
        chat_file = chat if isinstance(chat, UploadFile) and (chat.filename or "").strip() else None
        meeting_files.append((att, trans, chat_file))

    result = await collect_training_data(
        collector=current_user,
        identity_csv=identity_csv,
        github_urls=github_urls,
        google_doc_urls=google_doc_urls,
        meetings_meta=meetings_meta,
        meeting_files=meeting_files,
        db=db,
    )
    return success(
        data=result,
        message="Training data collected successfully.",
        code=status.HTTP_201_CREATED,
    )


@router.get(
    "/collections",
    response_model=ApiResponse[list[TrainingCollectionOut]],
    summary="List training collections for the current user",
)
async def list_collections(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    collections = await list_training_collections(current_user.id, db)
    return success(
        data=[TrainingCollectionOut.model_validate(item) for item in collections],
        message="Training collections retrieved successfully.",
    )


@router.get(
    "/collections/{collection_id}",
    response_model=ApiResponse[TrainingCollectionDetailOut],
    summary="Get training collection details",
)
async def get_collection(
    collection_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    detail = await get_training_collection_detail(collection_id, current_user.id, db)
    return success(data=detail, message="Training collection retrieved successfully.")
