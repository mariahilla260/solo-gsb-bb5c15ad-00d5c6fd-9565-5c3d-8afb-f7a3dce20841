"""HTTP 接口层测试。

依赖 fastapi + httpx（TestClient 基于 httpx）。
未安装时自动跳过；核心算法测试见 test_segmentation.py（纯标准库即可运行）。
"""

import unittest

try:
    from fastapi.testclient import TestClient
    from app.main import app
    _HAS_DEPS = True
except ImportError:  # pragma: no cover
    _HAS_DEPS = False


@unittest.skipUnless(_HAS_DEPS, "fastapi/httpx 未安装，跳过接口层测试")
class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_basic_segmentation(self):
        resp = self.client.post(
            "/segments",
            json={
                "words": [
                    {"start_ms": 0, "end_ms": 2000, "text": "第一句。", "speaker": "A"},
                    {"start_ms": 2000, "end_ms": 4200, "text": "第二句！", "speaker": "B"},
                    {"start_ms": 4200, "end_ms": 6200, "text": "第三句？", "speaker": "B"},
                ]
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_score"], 0)
        self.assertEqual(len(data["segments"]), 2)  # B 的两句合段更优（末段零分）
        self.assertIn("1\n00:00:00,000 --> 00:00:02,000", data["srt"])

    def test_overlap_rejected(self):
        resp = self.client.post(
            "/segments",
            json={
                "words": [
                    {"start_ms": 0, "end_ms": 2000, "text": "甲", "speaker": "A"},
                    {"start_ms": 1500, "end_ms": 3000, "text": "乙", "speaker": "A"},
                ]
            },
        )
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "overlapping_time_range")

    def test_out_of_order_rejected(self):
        resp = self.client.post(
            "/segments",
            json={
                "words": [
                    {"start_ms": 2000, "end_ms": 3000, "text": "甲", "speaker": "A"},
                    {"start_ms": 1000, "end_ms": 1500, "text": "乙", "speaker": "A"},
                ]
            },
        )
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "time_out_of_order")

    def test_infeasible_returns_earliest_prefix(self):
        resp = self.client.post(
            "/segments",
            json={
                "words": [
                    {"start_ms": 0, "end_ms": 800, "text": "短", "speaker": "A"},
                ]
            },
        )
        self.assertEqual(resp.status_code, 422)
        err = resp.json()["error"]
        self.assertEqual(err["code"], "infeasible_segmentation")
        self.assertEqual(err["last_word_index"], 0)

    def test_invalid_schema(self):
        resp = self.client.post("/segments", json={"words": []})
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "invalid_request")


@unittest.skipUnless(_HAS_DEPS, "fastapi/httpx 未安装，跳过接口层测试")
class AdBreakApiTests(unittest.TestCase):
    WORDS = [
        {"start_ms": 0, "end_ms": 500, "text": "甲", "speaker": "A"},
        {"start_ms": 1300, "end_ms": 1800, "text": "乙", "speaker": "A"},
        {"start_ms": 2700, "end_ms": 3200, "text": "丙", "speaker": "B"},
        {"start_ms": 3700, "end_ms": 4200, "text": "丁", "speaker": "B"},
    ]
    # 三处静音：[500,1300]=800ms（中点 900）、[1800,2700]=900ms（中点 2250）、
    # [3200,3700]=500ms（中点 3450）

    def setUp(self):
        self.client = TestClient(app)

    def post(self, **overrides):
        payload = {
            "words": self.WORDS,
            "break_count": 2,
            "min_silence_ms": 500,
            "min_spacing_ms": 0,
        }
        payload.update(overrides)
        return self.client.post("/ad-breaks", json=payload)

    def test_basic_ad_breaks(self):
        resp = self.post()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # 总静音最大：900 + 800（中点间隔 1350 与 1200 均不受 min_spacing=0 限制）
        self.assertEqual(data["total_silence_ms"], 1700)
        self.assertEqual(
            data["breaks"],
            [
                {
                    "index": 0,
                    "time_ms": 900,
                    "silence_start_ms": 500,
                    "silence_end_ms": 1300,
                    "silence_ms": 800,
                },
                {
                    "index": 1,
                    "time_ms": 2250,
                    "silence_start_ms": 1800,
                    "silence_end_ms": 2700,
                    "silence_ms": 900,
                },
            ],
        )

    def test_spacing_forces_spread(self):
        # 中点两两相距 1350/1200/2550：间隔 >= 2000 时只能取首尾
        resp = self.post(min_spacing_ms=2000)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual([b["time_ms"] for b in data["breaks"]], [900, 3450])
        self.assertEqual(data["total_silence_ms"], 1300)

    def test_min_silence_filters_candidates(self):
        # 阈值 850 只剩 900ms 一处候选，选 2 个无解
        resp = self.post(min_silence_ms=850)
        self.assertEqual(resp.status_code, 422)
        err = resp.json()["error"]
        self.assertEqual(err["code"], "infeasible_ad_breaks")
        self.assertEqual(err["candidate_count"], 1)

    def test_infeasible_spacing(self):
        resp = self.post(min_spacing_ms=100_000)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "infeasible_ad_breaks")

    def test_break_count_out_of_range(self):
        for bad in (0, 6):
            resp = self.post(break_count=bad)
            self.assertEqual(resp.status_code, 422)
            self.assertEqual(resp.json()["error"]["code"], "invalid_request")

    def test_negative_or_zero_thresholds_rejected(self):
        for field, bad in (("min_silence_ms", 0), ("min_spacing_ms", -1)):
            resp = self.post(**{field: bad})
            self.assertEqual(resp.status_code, 422)
            self.assertEqual(resp.json()["error"]["code"], "invalid_request")

    def test_word_validation_reused(self):
        words = [dict(self.WORDS[1]), dict(self.WORDS[0])]  # 时间倒序
        resp = self.post(words=words, break_count=1)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "time_out_of_order")

    def test_segments_still_works(self):
        resp = self.client.post("/segments", json={"words": self.WORDS})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("segments", resp.json())


@unittest.skipUnless(_HAS_DEPS, "fastapi/httpx 未安装，跳过接口层测试")
class SpeakerReconcileApiTests(unittest.TestCase):
    WORDS = [
        {"start_ms": 0, "end_ms": 500, "text": "甲", "speaker": "A"},
        {"start_ms": 500, "end_ms": 1000, "text": "乙", "speaker": "A"},
        {"start_ms": 1000, "end_ms": 1500, "text": "丙", "speaker": "B"},
        {"start_ms": 1500, "end_ms": 2000, "text": "丁", "speaker": "A"},
    ]

    def setUp(self):
        self.client = TestClient(app)

    def post(self, **overrides):
        payload = {"words": self.WORDS, "locks": [], "switch_cost": 5}
        payload.update(overrides)
        return self.client.post("/speaker-reconcile", json=payload)

    def test_basic_reconcile_smooths(self):
        # A A B A，switch_cost=5：保留原标注需 2 次换人（成本 10），
        # 词 2 改为 A 仅 1 次改动（成本 1）
        resp = self.post()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual([w["speaker"] for w in data["words"]], ["A"] * 4)
        self.assertEqual(data["changed_word_indices"], [2])
        self.assertEqual(data["total_cost"], 1)
        # 词序、文本、时间不变
        self.assertEqual(
            [(w["start_ms"], w["end_ms"], w["text"]) for w in data["words"]],
            [(0, 500, "甲"), (500, 1000, "乙"), (1000, 1500, "丙"), (1500, 2000, "丁")],
        )

    def test_lock_respected(self):
        # 锁定词 2 为 B，switch_cost=2：AABB 与 BBBB 同为成本 3，取字典序小的 AABB
        resp = self.post(locks=[{"word_index": 2, "speaker": "B"}], switch_cost=2)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual([w["speaker"] for w in data["words"]], ["A", "A", "B", "B"])
        self.assertEqual(data["changed_word_indices"], [3])
        self.assertEqual(data["total_cost"], 3)

    def test_lock_can_introduce_new_speaker(self):
        resp = self.post(locks=[{"word_index": 0, "speaker": "C"}], switch_cost=0)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["words"][0]["speaker"], "C")
        self.assertEqual(data["changed_word_indices"], [0])
        self.assertEqual(data["total_cost"], 1)

    def test_locks_may_be_omitted(self):
        resp = self.client.post(
            "/speaker-reconcile",
            json={"words": self.WORDS, "switch_cost": 5},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["changed_word_indices"], [2])

    def test_lock_index_out_of_range(self):
        for bad in (4, -1):
            resp = self.post(locks=[{"word_index": bad, "speaker": "A"}])
            self.assertEqual(resp.status_code, 422)
            err = resp.json()["error"]
            self.assertEqual(err["code"], "lock_index_out_of_range")
            self.assertEqual(err["word_index"], bad)

    def test_duplicate_lock(self):
        resp = self.post(
            locks=[{"word_index": 1, "speaker": "A"}, {"word_index": 1, "speaker": "B"}]
        )
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "duplicate_lock")

    def test_too_many_speakers(self):
        words = [
            {"start_ms": i * 500, "end_ms": (i + 1) * 500, "text": "字", "speaker": s}
            for i, s in enumerate("ABCDEFGH")
        ]
        resp = self.post(words=words, locks=[{"word_index": 0, "speaker": "Z"}])
        self.assertEqual(resp.status_code, 422)
        err = resp.json()["error"]
        self.assertEqual(err["code"], "too_many_speakers")
        self.assertEqual(err["speaker_count"], 9)

    def test_switch_cost_out_of_range(self):
        for bad in (-1, 11):
            resp = self.post(switch_cost=bad)
            self.assertEqual(resp.status_code, 422)
            self.assertEqual(resp.json()["error"]["code"], "invalid_request")

    def test_lock_schema_invalid(self):
        resp = self.post(locks=[{"word_index": 0}])  # 缺 speaker
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "invalid_request")

    def test_word_validation_reused(self):
        words = [dict(self.WORDS[1]), dict(self.WORDS[0])]  # 时间倒序
        resp = self.post(words=words)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"]["code"], "time_out_of_order")


if __name__ == "__main__":
    unittest.main()
