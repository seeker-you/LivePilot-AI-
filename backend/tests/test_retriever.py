import unittest

from app import retriever


class TokenizeTest(unittest.TestCase):
    def test_cjk_unigrams_and_bigrams(self):
        tokens = retriever.tokenize("物流")
        self.assertIn("物流", tokens)
        self.assertIn("物", tokens)
        self.assertIn("流", tokens)

    def test_ascii_words_lowercased(self):
        tokens = retriever.tokenize("SKU 123")
        self.assertIn("sku", tokens)
        self.assertIn("123", tokens)


class BM25Test(unittest.TestCase):
    def test_matching_doc_ranks_first(self):
        docs = [
            {
                "title": "物流与售后政策",
                "product": "通用",
                "category": "policy",
                "content": "现货发货 退换 运费险",
                "tags": "发货 物流",
            },
            {
                "title": "主推商品卖点",
                "product": "面霜",
                "category": "product",
                "content": "修护 敏感肌",
                "tags": "面霜",
            },
        ]
        ranked = retriever.bm25_rank(docs, "物流几天到")
        self.assertEqual(ranked[0][0], 0)
        self.assertGreater(ranked[0][1], 0)


class RRFFuseTest(unittest.TestCase):
    def test_consensus_document_wins(self):
        fused = retriever.rrf_fuse([[(1, 0.9), (0, 0.5)], [(1, 0.8), (0, 0.7)]])
        self.assertEqual(fused[0][0], 1)

    def test_single_list(self):
        fused = retriever.rrf_fuse([[(2, 1.0), (0, 0.5), (1, 0.2)]])
        self.assertEqual(fused[0][0], 2)


if __name__ == "__main__":
    unittest.main()

