"""章节时间对齐核心逻辑单元测试 + 暴力枚举对拍（纯标准库）。

暴力对拍独立枚举：按说话人切段、用 str.find 枚举每个 cue 的全部
（含重叠）命中，再用 itertools.product 枚举每章一条命中的全部组合，
校验相邻间隔，按 (总偏差升序, 全局字符位序列字典序升序) 取最优，
与 align_chapters 对比。
"""

import itertools
import random
import unittest
from types import SimpleNamespace

from app.chapter_align import align_chapters
from app.errors import DomainError


def W(start, end, text, speaker):
    return SimpleNamespace(start_ms=start, end_ms=end, text=text, speaker=speaker)


def C(title, cue, approx):
    return SimpleNamespace(title=title, cue=cue, approx_ms=approx)


def brute_force(words, chapters, min_gap_ms):
    """返回按章排列的 (time, global_pos, word_index, char_start, char_end) 最优序列；
    无可行方案返回 None。"""
    word_start = []
    off = 0
    for w in words:
        word_start.append(off)
        off += len(w.text)

    # 同说话人连续区间
    runs = []
    i = 0
    n = len(words)
    while i < n:
        j = i + 1
        while j < n and words[j].speaker == words[i].speaker:
            j += 1
        runs.append((i, j))
        i = j

    cand_lists = []
    for ch in chapters:
        cands = []
        for a, b in runs:
            parts = [words[k].text for k in range(a, b)]
            bounds = [0]
            for t in parts:
                bounds.append(bounds[-1] + len(t))
            joined = "".join(parts)
            L = len(ch.cue)
            p = joined.find(ch.cue)
            while p != -1:
                fw = 0
                while bounds[fw + 1] <= p:
                    fw += 1
                lw = 0
                while bounds[lw + 1] <= p + L - 1:
                    lw += 1
                cend = p + L - bounds[fw] if lw == fw else len(parts[fw])
                wi = a + fw
                cands.append(
                    (
                        words[wi].start_ms,
                        word_start[wi] + (p - bounds[fw]),
                        wi,
                        p - bounds[fw],
                        cend,
                    )
                )
                p = joined.find(ch.cue, p + 1)
        if not cands:
            return None
        cand_lists.append(cands)

    best = None  # (总偏差, 全局字符位元组, 候选元组)
    for combo in itertools.product(*cand_lists):
        if any(b[0] - a[0] < min_gap_ms for a, b in zip(combo, combo[1:])):
            continue
        total = sum(abs(cand[0] - ch.approx_ms) for cand, ch in zip(combo, chapters))
        pos_tuple = tuple(cand[1] for cand in combo)
        key = (total, pos_tuple)
        if best is None or key < (best[0], best[1]):
            best = (total, pos_tuple, combo)
    if best is None:
        return None
    return best[2]


def random_words(rng, n):
    words = []
    t = 0
    speaker = rng.choice("AB")
    alpha = "甲乙丙动态规划再见欢迎😀。！，ab"
    for _ in range(n):
        if rng.random() < 0.25:
            speaker = rng.choice("AB")
        dur = rng.randint(1, 800)
        gap = rng.choice([0, 0, 0, rng.randint(1, 500)])
        text = "".join(rng.choice(alpha) for _ in range(rng.randint(1, 4)))
        words.append(W(t, t + dur, text, speaker))
        t += dur + gap
    return words


def random_chapters(rng, words, k):
    joined = "".join(w.text for w in words)
    chapters = []
    for i in range(k):
        length = rng.randint(1, 3)
        p = rng.randrange(max(1, len(joined) - length))
        cue = joined[p : p + length]
        if not cue:
            cue = joined[:1]
        chapters.append(C(f"ch{i}", cue, rng.randint(0, words[-1].end_ms)))
    return chapters


class ChapterAlignBasicTests(unittest.TestCase):
    WORDS = [
        W(0, 1000, "大家好，", "A"),
        W(1000, 2000, "欢迎收听。", "A"),
        W(2000, 3000, "今天讲动态规划。", "B"),
        W(3000, 4000, "动态规划很常用。", "B"),
        W(4000, 5000, "下期再见。", "A"),
    ]
    # 全局文本：大家好，欢迎收听。今天讲动态规划。动态规划很常用。下期再见。

    def test_basic_alignment(self):
        chapters = [C("开篇", "欢迎", 900), C("正题", "动态", 2100), C("结尾", "再见", 4200)]
        result = align_chapters(self.WORDS, chapters, 500)
        self.assertEqual(
            result,
            [
                {"title": "开篇", "time_ms": 1000, "word_index": 1,
                 "char_start": 0, "char_end": 2},
                {"title": "正题", "time_ms": 2000, "word_index": 2,
                 "char_start": 3, "char_end": 5},
                {"title": "结尾", "time_ms": 4000, "word_index": 4,
                 "char_start": 2, "char_end": 4},
            ],
        )

    def test_cross_word_match(self):
        # 词 2“今天讲动态规划。”末三字符“规划。” + 词 3“动态规划很常用。”
        # 首两字“动态”——同说话人 B，跨词命中；首字符在词 2 偏移 5
        chapters = [C("跨词", "规划。动态", 2000)]
        result = align_chapters(self.WORDS, chapters, 1)
        self.assertEqual(result[0]["word_index"], 2)
        self.assertEqual(result[0]["char_start"], 5)
        self.assertEqual(result[0]["char_end"], 8)  # 截到词 2 末尾 len("今天讲动态规划。")=8
        self.assertEqual(result[0]["time_ms"], 2000)

    def test_match_does_not_cross_speaker_boundary(self):
        # “。下”横跨 B→A 边界（词 3 末“。”+词 4“下期再见。”），不得命中
        with self.assertRaises(DomainError) as ctx:
            align_chapters(self.WORDS, [C("x", "。下", 0)], 1)
        self.assertEqual(ctx.exception.code, "infeasible_chapter_alignment")
        self.assertEqual(ctx.exception.extra["chapter_count"], 1)

    def test_overlapping_occurrences(self):
        # "aaa" 中 cue "aa" 有两处重叠命中（偏移 0、1），时刻相同，
        # approx 等距时取全局字符位 0
        words = [W(0, 1000, "aaa", "A")]
        result = align_chapters(words, [C("x", "aa", 0)], 1)
        self.assertEqual(result[0]["char_start"], 0)
        self.assertEqual(result[0]["char_end"], 2)
        # approx 偏向第二处所在词时取偏移 1（词 1 "baa" 中的 "aa"）
        words2 = [W(0, 100, "aa", "A"), W(1000, 1100, "baa", "A")]
        result2 = align_chapters(words2, [C("x", "aa", 1000)], 1)
        self.assertEqual(result2[0]["word_index"], 1)
        self.assertEqual(result2[0]["char_start"], 1)

    def test_unicode_codepoints(self):
        # emoji 按 1 个 code point 计；cue "bc" 跨词内偏移：词 0 "😀ab"
        words = [W(0, 1000, "😀ab", "A"), W(1000, 2000, "c😀d", "A")]
        result = align_chapters(words, [C("x", "bc", 0)], 1)
        self.assertEqual(result[0]["word_index"], 0)
        self.assertEqual(result[0]["char_start"], 2)
        self.assertEqual(result[0]["char_end"], 3)

    def test_min_gap_enforced(self):
        # 三处命中时刻 1000/2000/4000；min_gap=1500 时第 1 章“欢迎”与
        # 第 2 章“动态”间隔恰好 1000 不达标 => 无解（无其他命中）
        chapters = [C("开篇", "欢迎", 900), C("正题", "动态", 2100), C("结尾", "再见", 4200)]
        with self.assertRaises(DomainError) as ctx:
            align_chapters(self.WORDS, chapters, 1500)
        self.assertEqual(ctx.exception.code, "infeasible_chapter_alignment")

    def test_same_word_multiple_occurrences_tie_break(self):
        # cue "动态" 在词 0“动态动态”中有两处命中（偏移 0、2），时刻相同。
        words = [
            W(0, 1000, "动态动态", "A"),
            W(5000, 6000, "收束", "A"),
        ]
        # 两章 cue 同为 "动态"：正整数 min_gap 下两章候选时刻都只有 0，
        # 间隔为 0 不达标 => 无解
        with self.assertRaises(DomainError):
            align_chapters(words, [C("a", "动态", 0), C("b", "动态", 0)], 1)
        # 单章两处命中偏差相同，取全局字符位 0
        result = align_chapters(words, [C("a", "动态", 0)], 1)
        self.assertEqual(result[0]["word_index"], 0)
        self.assertEqual(result[0]["char_start"], 0)

    def test_lex_tie_across_chapters(self):
        # 跨章同分裁决：词 0（t=0）“甲X甲”有两位甲（全局 0、2），
        # 词 1（t=10）“甲Y甲”有两位甲（全局 4、6）；min_gap=10 迫使
        # 两章分别落在词 0、词 1。approx=(5,5) 时词 0/词 1 的两位候选
        # 偏差分别恒为 5，四种组合总偏差相同（10），字符位序列字典序
        # 最小者为 ((词0,偏移0), (词1,偏移0))。
        words = [
            W(0, 5, "甲X甲", "A"),
            W(10, 15, "甲Y甲", "A"),
        ]
        result = align_chapters(words, [C("a", "甲", 5), C("b", "甲", 5)], 10)
        self.assertEqual(
            [(r["word_index"], r["char_start"]) for r in result],
            [(0, 0), (1, 0)],
        )

        # 单章同词同时刻的两位候选偏差相同，取全局字符位小者
        r = align_chapters([W(0, 100, "甲x甲", "A")], [C("a", "甲", 10_000)], 1)
        self.assertEqual(r[0]["char_start"], 0)

    def test_word_validation_reused(self):
        bad = [W(2000, 3000, "甲", "A"), W(0, 1000, "乙", "A")]  # 时间倒序
        with self.assertRaises(DomainError) as ctx:
            align_chapters(bad, [C("x", "乙", 0)], 1)
        self.assertEqual(ctx.exception.code, "time_out_of_order")

    def test_deviation_minimized(self):
        # 词 0 与词 2 都含 "甲"；approx=3000 时应选词 2
        words = [W(0, 1000, "甲一", "A"), W(1000, 2000, "乙", "B"),
                 W(3000, 4000, "二甲", "B")]
        result = align_chapters(words, [C("x", "甲", 3000)], 1)
        self.assertEqual(result[0]["word_index"], 2)
        self.assertEqual(result[0]["char_start"], 1)


class BruteForceTests(unittest.TestCase):
    def test_against_brute_force_random(self):
        rng = random.Random(20260924)
        trials = 2000
        for _ in range(trials):
            n = rng.randint(1, 10)
            words = random_words(rng, n)
            k = rng.randint(1, 3)
            chapters = random_chapters(rng, words, k)
            min_gap = rng.choice([1, 1, 200, 800, 2000])
            expected = brute_force(words, chapters, min_gap)
            if expected is None:
                with self.assertRaises(DomainError):
                    align_chapters(words, chapters, min_gap)
                continue
            result = align_chapters(words, chapters, min_gap)
            self.assertEqual(len(result), len(expected))
            for r, e in zip(result, expected):
                self.assertEqual(r["time_ms"], e[0])
                self.assertEqual(r["word_index"], e[2])
                self.assertEqual(r["char_start"], e[3])
                self.assertEqual(r["char_end"], e[4])
            for r, ch in zip(result, chapters):
                self.assertEqual(r["title"], ch.title)


if __name__ == "__main__":
    unittest.main()
