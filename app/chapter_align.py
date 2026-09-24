"""章节时间对齐核心逻辑（纯标准库，不依赖 FastAPI，便于单测）。

为按章节顺序排列的 chapters 在转写词序列中各找一处 cue 命中，使各章
实际时刻与预计起点 approx_ms 的绝对偏差之和最小。

命中规则：cue 在**同一说话人连续词**拼成的文本中查找全部 Unicode
（code point）子串命中，允许跨词但不允许跨越说话人边界；候选时刻取
命中首字符所在词的 start_ms。同一首字符位置至多一条候选，但同一词内
不同首字符位置各自独立（时刻可能相同，靠全局字符位区分）。

选择：每章恰选一条候选，相邻候选时刻至少相隔 min_gap_ms（正整数，
故时刻自然严格递增）；最小化 sum |time_i - approx_ms_i|；同分时取
“命中起始全局字符位”序列字典序最小者。全局字符位指把所有词文本原样
拼接（不插空格）后首字符的 0-based code point 偏移。

算法（后向 DP + 贪心重建），令 c_i 为第 i 章候选数：

* bwd[i][j]：第 i 章取其第 j 条候选时，后缀 [i, k) 的最小总偏差；
  候选时刻随列表下标非降，用二分定位下一章首个满足间隔的候选，
  配合下一行的后缀最小值完成转移，整体 O(sum c_i · log c_{i+1})；
* 最优值 = min_j bwd[0][j]；重建时从左向右贪心：在仍能以剩余偏差预算
  补全最优总分的候选中取全局字符位最小者（同一时刻可对应多个词内位置，
  字符位不随时刻单调，故须扫描全部可行候选），按字典序归纳即得整条
  字符位序列字典序最小。
"""

from bisect import bisect_left, bisect_right

from .errors import DomainError
from .segmentation import validate_words

INF = float("inf")


def _fail(code, message, **extra):
    raise DomainError(code, message, **extra)


def _speaker_runs(words):
    """同一说话人连续词区间：返回 [(起点词序号, 右开词界), ...]。"""
    runs = []
    i = 0
    n = len(words)
    while i < n:
        j = i + 1
        while j < n and words[j].speaker == words[i].speaker:
            j += 1
        runs.append((i, j))
        i = j
    return runs


def _find_run_candidates(words, run_a, run_b, cue, word_global_start):
    """在单个同说话人区间 [run_a, run_b) 内查找 cue 的全部命中。

    返回候选列表，每项为
    (time_ms, global_pos, word_index, char_start, char_end)；
    char_start/char_end 为命中首字符所在词内的 0-based、右开字符范围
    （按 code point）；命中跨词时 char_end 为该词文本长度。
    """
    parts = [words[k].text for k in range(run_a, run_b)]
    # 各词在拼接文本中的字符边界：boundaries[wi] 为词 wi 首字符的局部偏移
    boundaries = [0]
    for text in parts:
        boundaries.append(boundaries[-1] + len(text))

    joined = "".join(parts)
    cue_len = len(cue)
    out = []
    pos = joined.find(cue)
    while pos != -1:
        # 首字符所在词（边界处偏移属于后一词）与词内偏移
        first_wi = bisect_right(boundaries, pos) - 1
        first_inner = pos - boundaries[first_wi]
        # 末字符所在词；跨词时词内范围截到首词末尾
        last_wi = bisect_right(boundaries, pos + cue_len - 1) - 1
        if last_wi == first_wi:
            char_end = pos + cue_len - boundaries[first_wi]
        else:
            char_end = len(parts[first_wi])
        word_index = run_a + first_wi
        global_pos = word_global_start[word_index] + first_inner
        out.append(
            (
                words[word_index].start_ms,
                global_pos,
                word_index,
                first_inner,
                char_end,
            )
        )
        pos = joined.find(cue, pos + 1)  # 允许重叠命中
    return out


def align_chapters(words, chapters, min_gap_ms):
    """为每章选择一条 cue 命中，最小化对 approx_ms 的绝对偏差之和。

    参数 words 中每个对象需具备属性：start_ms / end_ms / text / speaker；
    chapters 中每项需具备属性：title / cue / approx_ms。

    返回按章排序的结果列表，每项为 dict：
    title / time_ms / word_index / char_start / char_end。
    任一章节无命中或无法组成完整方案时抛出
    DomainError(code="infeasible_chapter_alignment")（HTTP 422）。
    """
    validate_words(words)

    # 每个词首字符在“全词拼接文本”中的全局字符位
    word_global_start = []
    offset = 0
    for w in words:
        word_global_start.append(offset)
        offset += len(w.text)

    runs = _speaker_runs(words)

    # ---- 每章的全部候选：(time, global_pos, word_index, char_start, char_end) ----
    cand_lists = []
    for chapter in chapters:
        cands = []
        for run_a, run_b in runs:
            cands.extend(
                _find_run_candidates(
                    words, run_a, run_b, chapter.cue, word_global_start
                )
            )
        if not cands:
            _fail(
                "infeasible_chapter_alignment",
                "不存在满足全部约束的章节对齐方案：存在章节的 cue 无任何命中",
                chapter_count=len(chapters),
            )
        # 全局字符位随首字符位置唯一；时刻相同的候选靠字符位定序
        cands.sort(key=lambda c: (c[0], c[1]))
        cand_lists.append(cands)

    chapter_count = len(chapters)
    times_lists = [[c[0] for c in cands] for cands in cand_lists]

    # ---- 后向 DP ----
    # bwd[i][j]：第 i 章取候选 j 时后缀 [i, chapter_count) 的最小总偏差。
    bwd = [None] * chapter_count
    suff_rows = [None] * chapter_count  # 每行的后缀最小值，供转移与重建使用
    last_list = cand_lists[-1]
    last_approx = chapters[-1].approx_ms
    last_row = [abs(c[0] - last_approx) for c in last_list]
    bwd[-1] = last_row
    suff = last_row[:]
    for j in range(len(suff) - 2, -1, -1):
        if suff[j + 1] < suff[j]:
            suff[j] = suff[j + 1]
    suff_rows[-1] = suff

    for i in range(chapter_count - 2, -1, -1):
        cur_list = cand_lists[i]
        next_times = times_lists[i + 1]
        next_suff = suff_rows[i + 1]
        approx = chapters[i].approx_ms
        row = [INF] * len(cur_list)
        for j, c in enumerate(cur_list):
            k = bisect_left(next_times, c[0] + min_gap_ms)
            if k < len(next_times):
                tail = next_suff[k]
                if tail != INF:
                    row[j] = abs(c[0] - approx) + tail
        bwd[i] = row
        row_suff = row[:]
        for j in range(len(row_suff) - 2, -1, -1):
            if row_suff[j + 1] < row_suff[j]:
                row_suff[j] = row_suff[j + 1]
        suff_rows[i] = row_suff

    optimum = min(bwd[0])
    if optimum == INF:
        _fail(
            "infeasible_chapter_alignment",
            "不存在满足全部约束的章节对齐方案",
            chapter_count=chapter_count,
        )

    # ---- 贪心重建：每步在仍能达成最优总分的候选中取全局字符位最小者 ----
    results = []
    prev_time = None
    budget = optimum
    for i, chapter in enumerate(chapters):
        cur_list = cand_lists[i]
        chosen = None
        for c in cur_list:
            if prev_time is not None and c[0] - prev_time < min_gap_ms:
                continue
            deviation = abs(c[0] - chapter.approx_ms)
            if i + 1 < chapter_count:
                next_times = times_lists[i + 1]
                k = bisect_left(next_times, c[0] + min_gap_ms)
                tail = suff_rows[i + 1][k] if k < len(next_times) else INF
            else:
                tail = 0
            if tail == INF or deviation + tail != budget:
                continue
            # 同分裁决：全局字符位唯一，取最小者（列表时刻非降但字符位
            # 并不随之单调，故须扫全部可行候选）
            if chosen is None or c[1] < chosen[1]:
                chosen = c
        results.append(
            {
                "title": chapter.title,
                "time_ms": chosen[0],
                "word_index": chosen[2],
                "char_start": chosen[3],
                "char_end": chosen[4],
            }
        )
        budget -= abs(chosen[0] - chapter.approx_ms)
        prev_time = chosen[0]

    return results
