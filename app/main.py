"""FastAPI 入口：POST /segments 字幕分段、POST /ad-breaks 广告插播点、
POST /speaker-reconcile 说话人校正服务。"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .ad_breaks import optimal_ad_breaks
from .errors import DomainError
from .schemas import (
    AdBreakRequest,
    AdBreakResponse,
    SegmentRequest,
    SegmentResponse,
    SpeakerReconcileRequest,
    SpeakerReconcileResponse,
)
from .segmentation import optimal_segmentation
from .speaker_reconcile import reconcile_speakers
from .srt import render_srt

app = FastAPI(
    title="Podcast Subtitle Segmentation API",
    description="播客转写后处理：在硬约束下用动态规划求最优字幕分段（JSON 与 SRT），"
    "在词间静音处选择最优广告插播点，并在人工锁定约束下校正说话人标注。",
    version="1.2.0",
)


def _error_response(code: str, message: str, extra: dict | None = None):
    detail = {"code": code, "message": message}
    if extra:
        detail.update(extra)
    return JSONResponse(status_code=422, content={"error": detail})


@app.exception_handler(DomainError)
async def domain_error_handler(_: Request, exc: DomainError):
    return _error_response(exc.code, exc.message, exc.extra)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    return _error_response(
        "invalid_request",
        "请求体不合法：words 必须为非空数组，每项含非负整数 start_ms/end_ms、"
        "非空字符串 text/speaker；/ad-breaks 另需 break_count∈[1,5]、"
        "min_silence_ms≥1、min_spacing_ms≥0；/speaker-reconcile 另需 "
        "switch_cost∈[0,10]，locks 每项含整数 word_index 与非空 speaker",
        {"errors": exc.errors()},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/segments", response_model=SegmentResponse)
async def create_segments(request: SegmentRequest):
    words = request.words  # Pydantic 模型自带所需属性
    segments, total_score = optimal_segmentation(words)
    return SegmentResponse(
        segments=segments,
        total_score=total_score,
        srt=render_srt(segments),
    )


@app.post("/ad-breaks", response_model=AdBreakResponse)
async def create_ad_breaks(request: AdBreakRequest):
    breaks, total_silence_ms = optimal_ad_breaks(
        request.words,
        request.break_count,
        request.min_silence_ms,
        request.min_spacing_ms,
    )
    return AdBreakResponse(breaks=breaks, total_silence_ms=total_silence_ms)


@app.post("/speaker-reconcile", response_model=SpeakerReconcileResponse)
async def create_speaker_reconcile(request: SpeakerReconcileRequest):
    words, changed_word_indices, total_cost = reconcile_speakers(
        request.words,
        request.locks,
        request.switch_cost,
    )
    return SpeakerReconcileResponse(
        words=words,
        changed_word_indices=changed_word_indices,
        total_cost=total_cost,
    )
