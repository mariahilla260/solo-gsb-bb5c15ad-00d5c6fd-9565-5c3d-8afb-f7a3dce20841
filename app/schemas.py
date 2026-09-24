"""请求/响应 Pydantic 模型。"""

from pydantic import BaseModel, Field


class WordIn(BaseModel):
    start_ms: int = Field(..., ge=0, description="词起始时间（毫秒）")
    end_ms: int = Field(..., ge=0, description="词结束时间（毫秒）")
    text: str = Field(..., min_length=1, description="词文本；段文本为词文本原样拼接")
    speaker: str = Field(..., min_length=1, description="说话人标识；换人处强制断段")


class SegmentRequest(BaseModel):
    words: list[WordIn] = Field(..., min_length=1, description="按时间排列的词序列")


class SegmentOut(BaseModel):
    index: int
    start_word: int = Field(..., description="段首词序号（0-based，含）")
    end_word: int = Field(..., description="段末词序号（0-based，含）")
    start_ms: int
    end_ms: int
    duration_ms: int
    speaker: str
    text: str
    char_count: int = Field(..., description="Unicode 字符数（code point）")


class SegmentResponse(BaseModel):
    segments: list[SegmentOut]
    total_score: int
    srt: str


class AdBreakRequest(BaseModel):
    words: list[WordIn] = Field(..., min_length=1, description="按时间排列的词序列")
    break_count: int = Field(..., ge=1, le=5, description="插播点个数（1～5）")
    min_silence_ms: int = Field(..., ge=1, description="候选静音时长下限（毫秒，正整数）")
    min_spacing_ms: int = Field(..., ge=0, description="相邻插播点最小间隔（毫秒，非负整数）")


class AdBreakOut(BaseModel):
    index: int
    time_ms: int = Field(..., description="插播时间：静音区间的整数中点")
    silence_start_ms: int = Field(..., description="静音起点（前词 end_ms）")
    silence_end_ms: int = Field(..., description="静音终点（后词 start_ms）")
    silence_ms: int = Field(..., description="该处静音时长（毫秒）")


class AdBreakResponse(BaseModel):
    breaks: list[AdBreakOut] = Field(..., description="按时间升序的插播点")
    total_silence_ms: int = Field(..., description="选中静音的总毫秒数")


class SpeakerLockIn(BaseModel):
    word_index: int = Field(..., description="被锁定的词序号（0-based）")
    speaker: str = Field(..., min_length=1, description="该词必须使用的说话人标识")


class SpeakerReconcileRequest(BaseModel):
    words: list[WordIn] = Field(..., min_length=1, description="按时间排列的词序列")
    locks: list[SpeakerLockIn] = Field(
        default_factory=list,
        description="人工锁定项：指定某些词必须使用的说话人；可为空或省略",
    )
    switch_cost: int = Field(..., ge=0, le=10, description="相邻换人的单位代价（0～10）")


class SpeakerReconcileResponse(BaseModel):
    words: list[WordIn] = Field(
        ..., description="重指派说话人后的词序列；词序、文本、时间与输入一致"
    )
    changed_word_indices: list[int] = Field(
        ..., description="说话人被改动的词序号（0-based，升序）"
    )
    total_cost: int = Field(
        ..., description="最小总成本：改动词数 + switch_cost × 相邻换人次数"
    )


class ChapterIn(BaseModel):
    title: str = Field(..., min_length=1, description="章节标题")
    cue: str = Field(
        ..., min_length=1, description="章节定位文本；在同一说话人连续词文本中查找命中"
    )
    approx_ms: int = Field(..., ge=0, description="章节预计起点（毫秒，非负整数）")


class ChapterAlignRequest(BaseModel):
    words: list[WordIn] = Field(..., min_length=1, description="按时间排列的词序列")
    chapters: list[ChapterIn] = Field(
        ..., min_length=1, description="按章节顺序排列的章节（标题、非空 cue、预计起点）"
    )
    min_gap_ms: int = Field(..., ge=1, description="相邻章节命中时刻的最小间隔（毫秒，正整数）")


class ChapterAlignOut(BaseModel):
    index: int
    title: str = Field(..., description="章节标题（按输入章节顺序返回）")
    time_ms: int = Field(..., description="实际时刻：命中首字符所在词的 start_ms")
    word_index: int = Field(..., description="命中首字符所在词序号（0-based）")
    char_start: int = Field(
        ..., description="命中首字符在该词内的 Unicode 字符偏移（0-based，含）"
    )
    char_end: int = Field(
        ...,
        description="词内字符偏移区间右端（不含）；命中跨词时截断到命中首字符所在词词末",
    )


class ChapterAlignResponse(BaseModel):
    chapters: list[ChapterAlignOut] = Field(..., description="按章节顺序排列的对齐结果")
    total_deviation_ms: int = Field(
        ..., description="最小总绝对偏差：各章实际时刻与 approx_ms 的绝对偏差之和"
    )
