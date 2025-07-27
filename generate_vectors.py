import pandas as pd
import numpy as np
import pickle
from sentence_transformers import SentenceTransformer

# Excelファイルの読み込み
df = pd.read_excel("words.xlsx")

# 必要な列だけ取り出し（例：英単語だけをベクトル化）
english_words = df["English"].astype(str).tolist()

# モデルのロード（これはローカルで実行するため問題なし）
model = SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')

# ベクトルを生成（重い処理）
vectors = model.encode(english_words)

# ベクトルと単語情報を保存
with open("word_vectors.pkl", "wb") as f:
    pickle.dump(vectors, f)

with open("words_metadata.pkl", "wb") as f:
    pickle.dump(df.to_dict(orient="records"), f)

print("✅ .pkl ファイルを保存しました！")
