# 保存用スクリプト（make_word_vectors.py などに保存して実行）

from sentence_transformers import SentenceTransformer
import pandas as pd
import pickle

# 1. 単語一覧の読み込み（words.xlsx）
df = pd.read_excel("static/words.xlsx")

# 2. 単語の抽出（英語だけ）
english_words = df["English"].dropna().unique().tolist()

# 3. モデル読み込み
model = SentenceTransformer("paraphrase-MiniLM-L6-v2")

# 4. ベクトル生成
vectors = model.encode(english_words)

# 5. 辞書に変換
word_vectors = {word: vec for word, vec in zip(english_words, vectors)}

# 6. 保存
with open("word_vectors.pkl", "wb") as f:
    pickle.dump(word_vectors, f)

print("✅ 正常に保存されました（辞書形式）")
