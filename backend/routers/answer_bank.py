"""
Answer bank CRUD + test-lookup endpoint.

Routes:
  GET    /api/answer-bank               list all answers
  POST   /api/answer-bank               add answer
  PUT    /api/answer-bank/{id}          update answer
  DELETE /api/answer-bank/{id}          delete answer
  POST   /api/answer-bank/test-lookup   test what lookup returns for a label
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_session
from backend.models import AnswerBank
from backend.schemas import AnswerBankCreate, AnswerBankResponse
from backend.services.answer_bank import lookup_answer

router = APIRouter(prefix="/api/answer-bank", tags=["answer-bank"])


class TestLookupRequest(BaseModel):
    field_label: str
    field_type: str = "text"
    country: Optional[str] = None


@router.get("", response_model=list[AnswerBankResponse])
async def list_answers(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(AnswerBank).order_by(AnswerBank.key))
    return result.scalars().all()


@router.post("/test-lookup")
async def test_lookup(body: TestLookupRequest, session: AsyncSession = Depends(get_session)):
    """
    Test what the answer bank would return for a given field label.
    Useful for debugging and front-end config verification.
    """
    context = {"country": body.country} if body.country else None
    result = await lookup_answer(body.field_label, body.field_type, session, context)
    return result.model_dump()


@router.post("", response_model=AnswerBankResponse, status_code=201)
async def add_answer(body: AnswerBankCreate, session: AsyncSession = Depends(get_session)):
    existing = await session.execute(select(AnswerBank).where(AnswerBank.key == body.key))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Key '{body.key}' already exists")
    row = AnswerBank(**body.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@router.put("/{answer_id}", response_model=AnswerBankResponse)
async def update_answer(
    answer_id: int,
    body: AnswerBankCreate,
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(AnswerBank, answer_id)
    if not row:
        raise HTTPException(status_code=404, detail="Answer not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await session.commit()
    await session.refresh(row)
    return row


@router.delete("/{answer_id}", status_code=204)
async def delete_answer(answer_id: int, session: AsyncSession = Depends(get_session)):
    row = await session.get(AnswerBank, answer_id)
    if not row:
        raise HTTPException(status_code=404, detail="Answer not found")
    await session.delete(row)
    await session.commit()
