from __future__ import annotations

from aiogram import Router

from app.handlers.ai import ai_router
from app.handlers.misc import misc_router
from app.handlers.reports import report_router
from app.handlers.shift_checklist import shift_checklist_router
from app.handlers.shift import shift_router
from app.handlers.stock import stock_router


router = Router()
router.include_router(misc_router)
router.include_router(report_router)
router.include_router(shift_checklist_router)
router.include_router(shift_router)
router.include_router(stock_router)
router.include_router(ai_router)
