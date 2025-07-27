from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# 非常にシンプルな単語リスト
WORDS = ["apple", "application", "banana", "band", "camera", "cat", "dog", "document"]

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/suggest')
def suggest():
    query = request.args.get('q', '')
    if not query:
        return jsonify([])
    
    # 簡単な前方一致検索
    suggestions = [word for word in WORDS if word.startswith(query.lower())]
    return jsonify(suggestions)

if __name__ == '__main__':
    app.run(debug=True)