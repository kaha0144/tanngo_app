import os
import pickle
from flask import Flask, request, render_template, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
import random
from dotenv import load_dotenv
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from datetime import datetime, timedelta
from functools import wraps
from sqlalchemy import func
from flask import jsonify
load_dotenv() 
from fuzzywuzzy import fuzz

# --- 初期化 ------------------------------------------------------------------
app = Flask(__name__)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# --- 環境ごとの設定 ---
db_url = os.environ.get('DATABASE_URL')
if db_url:
    # Render や Heroku の場合
    app.config["SECRET_KEY"] = os.environ.get('SECRET_KEY', os.urandom(24).hex())
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url  # ← ここ重要！！
else:
    # ローカル開発環境
    app.config["SECRET_KEY"] = os.urandom(24).hex()
    db_info = {
        'user': 'myuser',
        'password': 'kaha0144',
        'host': 'localhost',
        'port': '5432',
        'database': 'postgres'
    }
    app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://{user}:{password}@{host}:{port}/{database}'.format(**db_info)
# --- DB設定 ---
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('SQLALCHEMY_DATABASE_URI')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
migrate = Migrate(app, db)

# --- モデル定義 ----------------------------------------------------------------
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False, unique=True)
    nickname = db.Column(db.String(100), nullable=False)
    password = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)

    def __repr__(self):
        return f"<User {self.username}>"

class QuizAttempt(db.Model):
    __tablename__ = 'quiz_attempts'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    
    user = db.relationship('User', backref=db.backref('attempts', lazy=True))

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- グローバル変数とヘルパー関数 --------------------------------------------------
try:
    full_df = pd.read_excel("static/words.xlsx")
    ALL_INDICES = list(full_df.index)
    print("✅ words.xlsx を正常に読み込みました。")
except FileNotFoundError:
    print("❌ エラー: words.xlsx が見つかりません。")
    full_df = pd.DataFrame(columns=["English", "Japanese"])
    ALL_INDICES = []
try:
    with open("static/word_vectors.pkl", "rb") as f:
        embeddings = pickle.load(f)
except FileNotFoundError:
        embeddings = None
        print("❌ エラー: word_vectors.pkl が見つかりません。")


def is_answer_similar(user_answer, correct_answer, threshold=60):  # ← 数値を調整
    user_ans_clean = user_answer.strip().lower()
    correct_ans_clean = correct_answer.strip().lower()

    try:
        from sentence_transformers import util
    except ImportError:
        # fuzzyマッチでスコアを計算
        similarity = fuzz.partial_ratio(user_ans_clean, correct_ans_clean)
        return similarity >= threshold

    # sentence_transformers 使えるとき（ローカル用）
    emb1 = embeddings.get(user_answer)
    emb2 = embeddings.get(correct_answer)
    if emb1 is not None and emb2 is not None:
        sim = util.cos_sim(emb1, emb2).item()
        return sim >= (threshold / 100.0)

    # fallback: fuzzyマッチ
    similarity = fuzz.partial_ratio(user_ans_clean, correct_ans_clean)
    return similarity >= threshold

# （以降のコードはそのまま）


def remove_mistake_from_all_lists(index_to_delete):
    """指定された単語IDを、永続・中断セッションを含む全ての間違いリストから完全に削除する"""
    # 1. 永続的なランダム間違いリストから削除
    random_mistakes = session.get("random_quiz_mistakes", [])
    session["random_quiz_mistakes"] = [m for m in random_mistakes if m.get('idx') != index_to_delete]

    # 2. 永続的な詳細学習間違い辞書から削除
    detailed_mistakes_dict = session.get("detailed_quiz_mistakes", {})
    if detailed_mistakes_dict:
        for range_key in list(detailed_mistakes_dict.keys()):
            detailed_mistakes_dict[range_key] = [m for m in detailed_mistakes_dict[range_key] if m.get('idx') != index_to_delete]
        session['detailed_quiz_mistakes'] = detailed_mistakes_dict
    
    # 3. 中断中のセッション間違いから削除
    saved_states = session.get('saved_states', {})
    if saved_states:
        for direction, saves in saved_states.items():
            if saves.get('random') and 'session_mistakes' in saves['random']:
                saves['random']['session_mistakes'] = [m for m in saves['random']['session_mistakes'] if m.get('idx') != index_to_delete]
            if saves.get('review') and 'session_mistakes' in saves['review']:
                saves['review']['session_mistakes'] = [m for m in saves['review']['session_mistakes'] if m.get('idx') != index_to_delete]
            if saves.get('detailed'):
                for range_key, state in saves['detailed'].items():
                    if 'session_mistakes' in state:
                        state['session_mistakes'] = [m for m in state['session_mistakes'] if m.get('idx') != index_to_delete]
    session['saved_states'] = saved_states
        
def commit_quiz_mistakes():
    """現在のクイズの間違い（IDと方向）を、永続リストにコミットする"""
    if not current_user.is_authenticated:
        return

    current_quiz_type = session.get('current_quiz_type')
    current_mistakes = session.get('current_quiz_mistakes_indices', [])
     
    if not current_quiz_type or not current_mistakes:
        return

    if current_quiz_type == 'random':
        mistake_list = session.setdefault('random_quiz_mistakes', [])
        for mistake_dict in current_mistakes:
            if mistake_dict not in mistake_list:
                mistake_list.append(mistake_dict)
        session['random_quiz_mistakes'] = mistake_list

    elif current_quiz_type == 'detailed':
        current_range = session.get('detailed_quiz_range')
        if not current_range: return
        
        range_key = f"{current_range[0]}-{current_range[1]}"
        mistakes_dict = session.setdefault('detailed_quiz_mistakes', {})
        range_mistake_list = mistakes_dict.setdefault(range_key, [])

        for mistake_dict in current_mistakes:
            if mistake_dict not in range_mistake_list:
                range_mistake_list.append(mistake_dict)
        
        mistakes_dict[range_key] = range_mistake_list
        session['detailed_quiz_mistakes'] = mistakes_dict
        
def _init_quiz_session(quiz_type, initial_rows=None, initial_seed=None, initial_index=0, initial_score=0, detailed_range=None, initial_session_mistakes=None):
    session['index'] = initial_index
    session['score'] = initial_score
    session['last_result'] = None
    session['user_answer_for_feedback'] = ""
    session['correct_english_for_feedback'] = ""
    session['correct_japanese_for_feedback'] = ""
    session['current_quiz_mistakes_indices'] = initial_session_mistakes if initial_session_mistakes is not None else []
    session['current_quiz_type'] = quiz_type
    session['show_feedback_and_next_button'] = False

    if quiz_type == 'random':
        session['quiz_seed'] = initial_seed if initial_seed is not None else random.randint(0, 100000)
        session['quiz_rows'] = None
        session.pop('detailed_quiz_range', None)
    elif quiz_type in ['retry', 'detailed']:
        session['quiz_seed'] = None
        session['quiz_rows'] = initial_rows
        if quiz_type == 'detailed' and detailed_range:
            session['detailed_quiz_range'] = detailed_range
        else:
            session.pop('detailed_quiz_range', None)

def get_quiz_rows_from_session_params(quiz_seed, fixed_quiz_rows):
    if quiz_seed is not None:
        shuffled_indices = ALL_INDICES.copy()
        random.Random(quiz_seed).shuffle(shuffled_indices)
        return shuffled_indices[:len(shuffled_indices)]
    elif fixed_quiz_rows is not None:
        return fixed_quiz_rows
    return []

def _clear_current_quiz_session_vars():
    keys_to_clear = [
        'index', 'score', 'quiz_seed', 'quiz_rows', 'total_questions', 'last_result',
        'user_answer_for_feedback', 'correct_english_for_feedback', 'correct_japanese_for_feedback',
        'current_quiz_mistakes_indices', 'current_quiz_type', 'show_feedback_and_next_button',
        'detailed_quiz_range', 'current_row_index'
    ]
    for key in keys_to_clear:
        session.pop(key, None)

# --- 認証ルート --------------------------------------------------------------
# app.py の /signup ルートを修正

@app.route("/signup", methods=["GET", "POST"])
def signup():
    flash("アカウントの新規作成は管理者にお問い合わせください。", "info")
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            #flash("ログインに成功しました！", "success")
            return redirect(url_for("menu"))
        else:
            flash("ユーザー名またはパスワードが間違っています。", "error")
            return redirect(url_for("login"))
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    session.clear()
    flash("ログアウトしました。", "info")
    return redirect(url_for("login"))

@app.route("/set_direction/<direction>")
@login_required
def set_direction(direction):
    if direction in ['ej', 'je']:
        session['quiz_direction'] = direction
    return redirect(url_for('menu'))

# --- メインメニュー -------------------------------------------------------------
@app.route("/")
@app.route("/menu")
@login_required
def menu():
    quiz_direction = session.get('quiz_direction', 'ej')
    saved_states_for_direction = session.get('saved_states', {}).get(quiz_direction, {})

    # --- ★★★ ここからが新しいランキング集計ロジック ★★★ ---
    one_week_ago = datetime.utcnow() - timedelta(days=7)
    
    # 直近1週間の解答数をユーザーごとに集計し、上位3名を取得
    top_users = db.session.query(
        User,
        func.count(QuizAttempt.id).label('weekly_attempts')
    ).join(QuizAttempt, User.id == QuizAttempt.user_id)\
    .filter(QuizAttempt.timestamp >= one_week_ago)\
    .group_by(User.id)\
    .order_by(func.count(QuizAttempt.id).desc())\
    .limit(3).all()
    # --- ★★★ ここまで ★★★

    return render_template("menu.html", 
        saved_random_state=saved_states_for_direction.get('random'),
        saved_detailed_states=saved_states_for_direction.get('detailed', {}),
        saved_review_state=saved_states_for_direction.get('review'),
        top_users=top_users # ★ ランキングデータをテンプレートに渡す
    )

# --- クイズ開始・再開ルート ----------------------------------------------------
@app.route('/start_new_random_quiz')
@login_required
def start_new_random_quiz():
    commit_quiz_mistakes()
    quiz_direction = session.get('quiz_direction', 'ej')
    if 'saved_states' in session and quiz_direction in session['saved_states']:
        session['saved_states'][quiz_direction].pop('random', None)
        session.modified = True
    
    _init_quiz_session('random')
    #flash("新しいランダムクイズを開始します。", "info")
    return redirect(url_for('quiz'))

@app.route('/resume_random_quiz')
@login_required
def resume_random_quiz():
    quiz_direction = session.get('quiz_direction', 'ej')
    saved_state = session.get('saved_states', {}).get(quiz_direction, {}).get('random')

    if not saved_state:
        flash("再開できるランダムクイズが見つかりませんでした。", "warning")
        return redirect(url_for('menu'))
    
    session['saved_states'][quiz_direction].pop('random', None)
    session.modified = True

    _init_quiz_session('random', 
        initial_seed=saved_state.get('seed'), 
        initial_index=saved_state.get('index', 0), 
        initial_score=saved_state.get('score', 0),
        initial_session_mistakes=saved_state.get('session_mistakes', [])
    )
    #flash("中断したランダムクイズを再開します。", "info")
    return redirect(url_for('quiz'))

@app.route("/learn_details")
@login_required
def learn_details():
    commit_quiz_mistakes()
    total_words = len(full_df)
    ranges = [(i + 1, min(i + 50, total_words)) for i in range(0, total_words, 50)]
    
    # ★★★ ここからが修正後のロジック ★★★
    # 現在の出題方向に応じた中断データを正しく取得する
    quiz_direction = session.get('quiz_direction', 'ej')
    saved_states = session.get('saved_states', {}).get(quiz_direction, {}).get('detailed', {})
    
    return render_template("learn_details.html", ranges=ranges, saved_detailed_states=saved_states)

@app.route('/start_detailed_quiz/<int:start_idx>/<int:end_idx>')
@login_required
def start_detailed_quiz(start_idx, end_idx):
    commit_quiz_mistakes()
    quiz_direction = session.get('quiz_direction', 'ej')
    range_key = f"{start_idx}-{end_idx}"
    
    if 'saved_states' in session and quiz_direction in session['saved_states'] and 'detailed' in session['saved_states'][quiz_direction]:
        session['saved_states'][quiz_direction]['detailed'].pop(range_key, None)
        session.modified = True

    selected_indices = ALL_INDICES[start_idx - 1 : end_idx]
    _init_quiz_session('detailed', initial_rows=selected_indices, detailed_range=(start_idx, end_idx))
    #flash(f"詳細学習クイズ (範囲: {range_key}) を開始します。", "info")
    return redirect(url_for('quiz'))

@app.route('/resume_detailed_quiz/<range_key>')
@login_required
def resume_detailed_quiz(range_key):
    quiz_direction = session.get('quiz_direction', 'ej')
    saved_detailed_states = session.get('saved_states', {}).get(quiz_direction, {}).get('detailed', {})
    saved_state = saved_detailed_states.pop(range_key, None)

    if not saved_state:
        flash("再開できる詳細学習クイズが見つかりませんでした。", "warning")
        return redirect(url_for('menu'))
    
    session['saved_states'][quiz_direction]['detailed'] = saved_detailed_states
    session.modified = True

    start_idx, end_idx = map(int, range_key.split('-'))
    _init_quiz_session(
        'detailed',
        initial_rows=saved_state.get('rows'),
        initial_index=saved_state.get('index', 0),
        initial_score=saved_state.get('score', 0),
        detailed_range=(start_idx, end_idx),
        initial_session_mistakes=saved_state.get('session_mistakes', [])
    )
    #flash(f"中断した詳細学習クイズ (範囲: {range_key}) を再開します。", "info")
    return redirect(url_for('quiz'))

@app.route("/retry")
@login_required
def retry_mistakes():
    commit_quiz_mistakes()
    quiz_direction = session.get('quiz_direction', 'ej')
    saved_review_state = session.get('saved_states', {}).get(quiz_direction, {}).get('review')
    if saved_review_state:
        session['saved_states'][quiz_direction].pop('review', None)
        session.modified = True
        _init_quiz_session('retry', initial_rows=saved_review_state.get('rows'), initial_index=saved_review_state.get('index', 0), initial_score=saved_review_state.get('score', 0), initial_session_mistakes=saved_review_state.get('session_mistakes', []))
        return redirect(url_for('quiz'))
    
    random_mistakes = session.get("random_quiz_mistakes", [])
    detailed_mistakes_dict = session.get("detailed_quiz_mistakes", {})
    all_mistakes = list(random_mistakes)
    if detailed_mistakes_dict:
        for key in detailed_mistakes_dict:
            all_mistakes.extend(detailed_mistakes_dict[key])
    
    unique_mistakes_tuples = set(tuple(d.items()) for d in all_mistakes)
    unique_mistakes = [dict(t) for t in unique_mistakes_tuples]
    
    if not unique_mistakes:
        # ★★★ 修正箇所: メッセージを表示するページにリダイレクト ★★★
        return redirect(url_for("manage_mistakes"))

    random.shuffle(unique_mistakes)
    _init_quiz_session('retry', initial_rows=unique_mistakes) 
    return redirect(url_for("quiz"))

# --- クイズ進行・結果ルート -------------------------------------------------------
@app.route("/quiz", methods=["GET", "POST"])
@login_required
def quiz():
    quiz_type = session.get('current_quiz_type')
    global_quiz_direction = session.get('quiz_direction', 'ej')
    quiz_rows = get_quiz_rows_from_session_params(session.get('quiz_seed'), session.get('quiz_rows'))
    session['total_questions'] = len(quiz_rows)
    if not quiz_rows:
        flash("クイズセッションが正しく開始されていません。", "error")
        return redirect(url_for("menu"))

    idx = session.get("index", 0)
    if idx >= len(quiz_rows):
        return redirect(url_for("result"))
    
    current_question_item = quiz_rows[idx]
    
    if quiz_type == 'retry':
        row_index = current_question_item['idx']
        question_direction = current_question_item['dir']
    else:
        row_index = current_question_item
        question_direction = global_quiz_direction

    english_word = str(full_df.at[row_index, "English"]).strip()
    japanese_word = str(full_df.at[row_index, "Japanese"]).strip()
    question, correct_answer = (english_word, japanese_word) if question_direction == 'ej' else (japanese_word, english_word)
    
    hints = {}
    if question_direction == 'je':
        hints['first_letter'] = correct_answer[0] if correct_answer else ''
        hints['placeholder'] = ' '.join(['_' for _ in correct_answer])
        hints['word_length'] = len(correct_answer)

    if request.method == "POST":
        user_answer = request.form.get("user_answer", "").strip()
        correct = (user_answer.lower() == correct_answer.lower()) if question_direction == 'je' else is_answer_similar(user_answer, correct_answer)

        session['last_result'] = "正解" if correct else "不正解"
        session['user_answer_for_feedback'] = user_answer
        session['correct_english_for_feedback'] = english_word
        session['correct_japanese_for_feedback'] = japanese_word
        
        current_mistakes = session.get('current_quiz_mistakes_indices', [])
        mistake_to_check = {'idx': row_index, 'dir': question_direction}

        if correct:
            session["score"] = session.get("score", 0) + 1
            if mistake_to_check in current_mistakes:
                current_mistakes.remove(mistake_to_check)
                session['current_quiz_mistakes_indices'] = current_mistakes
        else:
            if mistake_to_check not in current_mistakes:
                current_mistakes.append(mistake_to_check)
                session['current_quiz_mistakes_indices'] = current_mistakes
        attempt = QuizAttempt(user_id=current_user.id)
        db.session.add(attempt)
        db.session.commit()
        session['show_feedback_and_next_button'] = True

    session['current_row_index'] = row_index
    template_to_render = "mistake.html" if quiz_type == 'retry' else "quiz.html"
    return render_template(
        template_to_render,
        question=question,
        result=session.get("last_result"),
        user_answer_for_feedback=session.get("user_answer_for_feedback"),
        correct_english_for_feedback=session.get("correct_english_for_feedback"),
        correct_japanese_for_feedback=session.get("correct_japanese_for_feedback"),
        current_question_number=idx + 1,
        total_questions=len(quiz_rows),
        show_feedback_and_next_button=session.get('show_feedback_and_next_button', False),
        hints=hints
    )

@app.route("/next_question")
@login_required
def next_question():
    session["index"] = session.get("index", 0) + 1
    session['show_feedback_and_next_button'] = False
    session.pop('user_answer_for_feedback', None)
    session.pop('correct_english_for_feedback', None)
    session.pop('correct_japanese_for_feedback', None)
    
    quiz_rows = get_quiz_rows_from_session_params(session.get('quiz_seed'), session.get('quiz_rows'))
    if session["index"] >= len(quiz_rows):
        return redirect(url_for("result"))
    
    return redirect(url_for("quiz"))

@app.route("/result")
@login_required
def result():
    commit_quiz_mistakes()
    score = session.get("score", 0)
    total = session.get("total_questions", 0)
    
    # 結果画面では間違いリストは表示しないので、シンプルにクリアしてリダイレクト
    _clear_current_quiz_session_vars()
    return render_template("result.html", score=score, total=total)

@app.route("/current_result")
@login_required
def current_result():
    active_mistakes = session.get('current_quiz_mistakes_indices', [])
    unique_indices = {mistake['idx'] for mistake in active_mistakes}
    
    mistake_words = []
    for idx in sorted(list(unique_indices)):
        mistake_words.append({
            'english': full_df.at[idx, 'English'],
            'japanese': full_df.at[idx, 'Japanese']
        })
    return render_template(
        "current_result.html",
        score=session.get("score", 0),
        current_question_number=session.get("index", 0),
        # ★★★ この行を修正 ★★★
        total_questions=session.get("total_questions", 0),
        mistake_words=mistake_words
    )

@app.route("/exit_quiz_to_menu")
@login_required
def exit_quiz_to_menu():
    session_mistakes_to_save = session.get('current_quiz_mistakes_indices', [])
    commit_quiz_mistakes()

    current_quiz_type = session.get('current_quiz_type')
    if not current_quiz_type:
        _clear_current_quiz_session_vars()
        return redirect(url_for("menu"))

    state_to_save = {
        'type': current_quiz_type,
        'index': session.get('index', 0),
        'score': session.get('score', 0),
        'seed': session.get('quiz_seed'),
        'rows': session.get('quiz_rows'),
        'session_mistakes': session_mistakes_to_save
    }
    
    quiz_direction = session.get('quiz_direction', 'ej')
    saved_states_by_direction = session.setdefault('saved_states', {})
    direction_saves = saved_states_by_direction.setdefault(quiz_direction, {})

    if current_quiz_type == 'random':
        direction_saves['random'] = state_to_save
        #flash(f"ランダムクイズ({quiz_direction})の進行状況を保存しました。", "info")
    elif current_quiz_type == 'detailed':
        current_range = session.get('detailed_quiz_range')
        if current_range:
            detailed_saves = direction_saves.setdefault('detailed', {})
            range_key = f"{current_range[0]}-{current_range[1]}"
            detailed_saves[range_key] = state_to_save
            #flash(f"詳細クイズ({quiz_direction}) (範囲: {range_key}) の進行状況を保存しました。", "info")
    elif current_quiz_type == 'retry':
        direction_saves['review'] = state_to_save
        #flash(f"復習クイズ({quiz_direction})の進行状況を保存しました。", "info")
        
    session['saved_states'] = saved_states_by_direction
    _clear_current_quiz_session_vars()
    return redirect(url_for("menu"))

@app.route("/remove_single_mistake/<int:row_index>")
@login_required
def remove_single_mistake(row_index):
    remove_mistake_from_all_lists(row_index)
    word_to_remove = full_df.at[row_index, "English"]
    flash(f"「{word_to_remove}」を復習リストから完全に削除しました。", "info")
    return redirect(url_for('next_question'))

@app.route('/start_fresh_quiz_from_anywhere')
@login_required
def start_fresh_quiz_from_anywhere():
    """全ての進行状況と間違いリストをリセットする"""
    _clear_current_quiz_session_vars()
    session.pop('saved_states', None)
    session.pop('random_quiz_mistakes', None)
    session.pop('detailed_quiz_mistakes', None)
    flash("全ての進行状況と間違いリストをリセットしました。", "info")
    return redirect(url_for('menu'))

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            flash("このページにアクセスする権限がありません。", "danger")
            return redirect(url_for('menu'))
        return f(*args, **kwargs)
    return decorated_function

@app.route("/admin", methods=["GET", "POST"])
@login_required
@admin_required
def admin_page():
    if request.method == "POST":
        # 新規ユーザー作成処理
        username = request.form.get("username")
        password = request.form.get("password")
        nickname = request.form.get("nickname")
        is_admin = 'is_admin' in request.form

        if not username or not password or not nickname:
            flash("すべてのフィールドを入力してください。", "warning")
        elif User.query.filter_by(username=username).first():
            flash("そのユーザー名は既に使用されています。", "warning")
        else:
            hashed_pass = generate_password_hash(password, method="pbkdf2:sha256")
            new_user = User(username=username, password=hashed_pass, nickname=nickname, is_admin=is_admin)
            db.session.add(new_user)
            db.session.commit()
            flash(f"ユーザー「{nickname}」が作成されました。", "success")
        return redirect(url_for('admin_page'))

    # --- 学習状況の集計 ---
    one_week_ago = datetime.utcnow() - timedelta(days=7)
    users = User.query.order_by(User.id).all()
    
    user_stats = []
    for user in users:
        # 直近1週間の解答数をカウント
        attempt_count = QuizAttempt.query.filter(
            QuizAttempt.user_id == user.id,
            QuizAttempt.timestamp >= one_week_ago
        ).count()
        user_stats.append({'user': user, 'weekly_attempts': attempt_count})

    return render_template("admin.html", user_stats=user_stats)

@app.route("/admin/delete_user/<int:user_id>", methods=["POST"])
@login_required
@admin_required
def delete_user(user_id):
    user_to_delete = User.query.get_or_404(user_id)
    if user_to_delete.id == current_user.id:
        flash("自分自身のアカウントは削除できません。", "danger")
    else:
        # 関連する学習履歴も削除
        QuizAttempt.query.filter_by(user_id=user_id).delete()
        db.session.delete(user_to_delete)
        db.session.commit()
        flash(f"ユーザー「{user_to_delete.nickname}」を削除しました。", "success")
    return redirect(url_for('admin_page'))

@app.route("/search", methods=["GET", "POST"])
@login_required
def search_word():
    search_results = []
    query = ""
    if request.method == "POST":
        query = request.form.get("query", "").strip()
        if query:
            # 英単語と日本語訳の両方から部分一致で検索
            results_df = full_df[
                full_df['English'].str.contains(query, case=False, na=False) |
                full_df['Japanese'].str.contains(query, case=False, na=False)
            ]
            search_results = results_df.to_dict('records')
    return render_template("search.html", search_results=search_results, query=query)

@app.route("/progress")
@login_required
def progress():
    # 直近7日間の学習データを集計
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    
    attempts_by_day = db.session.query(
        func.date(QuizAttempt.timestamp).label('date'),
        func.count(QuizAttempt.id).label('count')
    ).filter(
        QuizAttempt.user_id == current_user.id,
        QuizAttempt.timestamp >= seven_days_ago
    ).group_by(func.date(QuizAttempt.timestamp))\
    .order_by(func.date(QuizAttempt.timestamp)).all()

    # グラフ用にデータを整形
    labels = []
    data = []
    # 7日間の日付ラベルを生成
    date_labels = [(datetime.utcnow() - timedelta(days=i)).strftime('%m/%d') for i in range(6, -1, -1)]
    date_data = {label: 0 for label in date_labels}

    for attempt in attempts_by_day:
        # タイムゾーンを考慮せず日付のみで比較
        date_str = attempt.date.strftime('%m/%d')
        if date_str in date_data:
            date_data[date_str] = attempt.count
            
    labels = list(date_data.keys())
    data = list(date_data.values())

    return render_template("progress.html", labels=labels, data=data)

@app.route("/manage_mistakes", methods=["GET", "POST"])
@login_required
def manage_mistakes():
    if request.method == "POST":
        indices_to_delete = [int(i) for i in request.form.getlist('delete_indices')]
        if not indices_to_delete:
            flash("削除する単語が選択されていません。", "warning")
            return redirect(url_for('manage_mistakes'))
        for index_to_delete in indices_to_delete:
            remove_mistake_from_all_lists(index_to_delete)
        flash(f"{len(indices_to_delete)}件の単語をリストから完全に削除しました。", "success")
        return redirect(url_for('manage_mistakes'))

    random_mistakes = session.get("random_quiz_mistakes", [])
    detailed_mistakes_dict = session.get("detailed_quiz_mistakes", {})
    all_mistake_indices = set()
    for m in random_mistakes: all_mistake_indices.add(m['idx'])
    if detailed_mistakes_dict:
        for key in detailed_mistakes_dict:
            for m in detailed_mistakes_dict[key]:
                all_mistake_indices.add(m['idx'])
    
    mistake_words = []
    for idx in sorted(list(all_mistake_indices)):
        mistake_words.append({
            'index': idx,
            'english': full_df.at[idx, 'English'],
            'japanese': full_df.at[idx, 'Japanese']
        })
    return render_template("manage_mistakes.html", mistake_words=mistake_words)

@app.route("/mypage", methods=["GET", "POST"])
@login_required
def mypage():
    if request.method == "POST":
        action = request.form.get("action")

        # ニックネームの変更
        if action == "update_nickname":
            new_nickname = request.form.get("nickname")
            if new_nickname:
                current_user.nickname = new_nickname
                db.session.commit()
                flash("ニックネームを更新しました。", "success")
            else:
                flash("ニックネームを入力してください。", "warning")

        # ユーザー名の変更
        elif action == "update_username":
            new_username = request.form.get("username")
            if new_username:
                existing_user = User.query.filter(User.username == new_username, User.id != current_user.id).first()
                if existing_user:
                    flash("そのユーザー名は既に使用されています。", "danger")
                else:
                    current_user.username = new_username
                    db.session.commit()
                    flash("ユーザー名を更新しました。", "success")
            else:
                flash("ユーザー名を入力してください。", "warning")

        # パスワードの変更
        elif action == "update_password":
            current_password = request.form.get("current_password")
            new_password = request.form.get("new_password")
            confirm_password = request.form.get("confirm_password")

            if not check_password_hash(current_user.password, current_password):
                flash("現在のパスワードが正しくありません。", "danger")
            elif new_password != confirm_password:
                flash("新しいパスワードが一致しません。", "danger")
            elif not new_password:
                flash("新しいパスワードを入力してください。", "warning")
            else:
                current_user.password = generate_password_hash(new_password, method="pbkdf2:sha256")
                db.session.commit()
                flash("パスワードを更新しました。", "success")
        
        return redirect(url_for('mypage'))

    return render_template("mypage.html")

@app.route("/api/search_suggestions")
@login_required
def search_suggestions():
    query = request.args.get('q', '').strip()
    
    # queryが空の場合は、何も返さずに処理を終了
    if not query:
        return jsonify([])

    # 英語と日本語の両方のカラムで前方一致する行を一度にフィルタリング（こちらの方が効率的）
    condition = (
        full_df['English'].str.startswith(query, na=False, case=False) | 
        full_df['Japanese'].str.startswith(query, na=False, case=False)
    )
    matches_df = full_df[condition]

    # マッチした行から英語と日本語の候補を抽出し、重複を除外
    eng_suggestions = matches_df['English'].dropna()
    jpn_suggestions = matches_df['Japanese'].dropna()
    
    # 結合して、重複を除外し、リストに変換
    suggestions = pd.concat([eng_suggestions, jpn_suggestions]).unique().tolist()

    # [修正1] 候補を最大10件に絞り込む（構文エラーの修正）
    limited_suggestions = suggestions[:10]

    # [修正2] 検索候補をJSON形式でフロントエンドに返す
    return jsonify(limited_suggestions)
@app.route("/rough_menu")
@login_required
def rough_menu():
    return render_template("rough_menu.html")


@app.route("/start_rough_quiz/<direction>")
@login_required
def start_rough_quiz(direction):
    if direction not in ['je', 'ej']:
        flash("無効な出題方向です。", "danger")
        return redirect(url_for("menu"))
    
    indices = random.sample(ALL_INDICES, min(10, len(ALL_INDICES)))
    session['quiz_direction'] = direction
    session['quiz_rows'] = indices
    session['quiz_type'] = f"rough_{direction}"
    session['index'] = 0
    session['score'] = 0
    session['rough_mistakes'] = session.get('rough_mistakes', { 'rough_je': [], 'rough_ej': [] })
    return redirect(url_for("rough_quiz"))

@app.route("/start_rough_review")
@login_required
def start_rough_review():
    # 1. 永続リストと一時リストの両方から間違いを集める
    all_mistakes = session.get('global_rough_mistakes', [])
    
    temp_mistakes = session.get('rough_mistakes', {})
    for direction in ['rough_je', 'rough_ej']:
        all_mistakes.extend(temp_mistakes.get(direction, []))

    # 2. 【重要】リストから重複を削除する
    unique_mistakes = []
    seen_indices = set()
    for mistake in all_mistakes:
        # (単語のID, 出題方向) の組み合わせで重複をチェック
        identifier = (mistake['idx'], mistake['dir'])
        if identifier not in seen_indices:
            unique_mistakes.append(mistake)
            seen_indices.add(identifier)

    # 3. 重複削除後のリストでクイズを開始する
    if not unique_mistakes:
        flash("ざっくり復習する単語がありません。", "warning")
        return redirect(url_for("rough_menu"))
        
    random.shuffle(unique_mistakes)
    session['quiz_type'] = 'rough_review'
    session['quiz_rows'] = unique_mistakes  # 重複がないリストを使用
    session['index'] = 0
    session['score'] = 0
    return redirect(url_for("rough_quiz"))

@app.route("/rough_quiz", methods=["GET", "POST"])
@login_required
def rough_quiz():
    # セッションに rough_mistakes キーがなければ初期化
    if 'rough_mistakes' not in session:
        session['rough_mistakes'] = {'rough_je': [], 'rough_ej': []}

    idx = session.get("index", 0)
    quiz_rows = session.get("quiz_rows", [])

    # 全問終了したら結果画面へ
    if idx >= len(quiz_rows):
        return redirect(url_for("rough_result"))

    # 出題対象の単語ID と方向を決定
    quiz_type = session.get("quiz_type")
    if quiz_type == "rough_review":
        item = quiz_rows[idx]
        row_index = item['idx']
        direction = item['dir']
    else:
        row_index = quiz_rows[idx]
        direction = session.get("quiz_direction")

    english = full_df.at[row_index, "English"]
    japanese = full_df.at[row_index, "Japanese"]
    question, answer = (japanese, english) if direction == 'je' else (english, japanese)

    # 4択の選択肢を生成
    options = [answer]
    while len(options) < 4:
        candidate = full_df.sample(1).iloc[0]
        opt = candidate["English"] if direction == 'je' else candidate["Japanese"]
        if opt != answer and opt not in options:
            options.append(opt)
    random.shuffle(options)

    # フィードバック用変数初期化
    show_fb = False
    result = None
    user_ans = None
    correct_eng = english
    correct_jpn = japanese

    # ★追加: 範囲指定クイズかどうかを判定するフラグ
    is_ranged_quiz = 'rough_range' in session

    if request.method == "POST":
        user_ans = request.form.get("option")
        # 正誤判定
        is_correct = user_ans == answer
        # 1. スコアを更新
        session["score"] = session.get("score", 0) + int(is_correct)

        ## ★★★ ここからインデントを修正 ★★★
        if not is_correct and quiz_type != "rough_review":
            # このifブロックの中に、間違い記録処理をすべてまとめる
            entry = {'idx': row_index, 'dir': direction}

            # 2a. 現在のクイズ用の一時リストに記録
            key = f"rough_{direction}"
            mistakes = session['rough_mistakes'].get(key, [])
            if entry not in mistakes:
                mistakes.append(entry)
                session['rough_mistakes'][key] = mistakes

            # 2b. 復習用の永続リストに記録 (正しくインデント)
            if 'global_rough_mistakes' not in session:
                session['global_rough_mistakes'] = []
            global_mistakes = session.get('global_rough_mistakes', [])
            if entry not in global_mistakes:
                global_mistakes.append(entry)
                session['global_rough_mistakes'] = global_mistakes
        
        # 3. 回答直後にインデックスを更新して進捗を保存
        session["index"] = idx + 1
        
        # フィードバック表示
        show_fb = True
        result = "正解" if is_correct else "不正解"

        return render_template(
            "rough_quiz.html",
            question=question,
            options=options,
            idx=idx + 1,
            total=len(quiz_rows),
            quiz_rows=quiz_rows, 
            show_feedback_and_next_button=show_fb,
            result=result,
            user_answer_for_feedback=user_ans,
            correct_english_for_feedback=correct_eng,
            correct_japanese_for_feedback=correct_jpn,
            is_ranged_quiz=is_ranged_quiz
        )

    # （...GETリクエストのコードは変更なし...）
    return render_template(
        "rough_quiz.html",
        question=question,
        options=options,
        idx=idx + 1,
        total=len(quiz_rows),
        show_feedback_and_next_button=False,
        is_ranged_quiz=is_ranged_quiz
    )
@app.route("/rough_next_question")
@login_required
def rough_next_question():
    session["index"] = session.get("index", 0) 
    return redirect(url_for("rough_quiz"))


@app.route("/rough_range/<direction>")
@login_required
def rough_range_selector(direction):
    if direction not in ['je', 'ej']:
        flash("無効な出題方向です。", "danger")
        return redirect(url_for('rough_menu'))

    # 単語範囲の生成（例：1〜50、51〜100...）
    total_words = len(full_df)
    ranges = [(i + 1, min(i + 50, total_words)) for i in range(0, total_words, 50)]

    # 保存された進捗（存在する場合）
    saved_rough_states = session.get('saved_rough_states', {}).get(direction, {})
    return render_template(
        "rough_range_selector.html",
        direction=direction,
        ranges=ranges,
        saved_rough_states=saved_rough_states
    )

@app.route('/start_rough_quiz_with_range/<direction>/<int:start>/<int:end>')
@login_required
def start_rough_quiz_with_range(direction, start, end):
    if direction not in ['je', 'ej']:
        flash("無効な方向です", "danger")
        return redirect(url_for('rough_menu'))

    selected_df = full_df.iloc[start-1:end].copy()

    if selected_df.empty:
        flash("選択された範囲に単語が存在しません", "warning")
        return redirect(url_for('rough_menu'))

    selected_rows = selected_df.sample(min(50, len(selected_df)), replace=False).reset_index()

    session['quiz_type'] = 'rough'
    session['quiz_direction'] = direction
    session['quiz_rows'] = list(selected_rows['index'])  # 重要: 元の full_df の index
    session['index'] = 0  # ← 修正ポイント
    session['score'] = 0
    session['rough_mistakes'] = session.get('rough_mistakes', { 'rough_je': [], 'rough_ej': [] })

    session['rough_range'] = (start, end)
    return redirect(url_for('rough_quiz'))

@app.route("/rough_current_result")
@login_required
def rough_current_result():
    quiz_rows = session.get("quiz_rows", [])
    current_index = session.get("index", 0)
    score = session.get("score", 0)
    direction = session.get("quiz_direction", "je")
    quiz_type = session.get("quiz_type", "rough")

    # mistake_wordsは rough_mistakes から取り出す（復習は別扱い）
    mistakes = []
    if quiz_type == "rough_review":
        mistakes = session.get("quiz_rows", [])
    elif quiz_type in ['rough_je', 'rough_ej', 'rough']:
        key = f"rough_{direction}"
        mistakes = session.get("rough_mistakes", {}).get(key, [])

    return render_template(
        "rough_current_result.html",
        current_question_number=current_index + 1,
        total_questions=len(quiz_rows),
        score=score,
        mistake_words=[
            {"english": full_df.at[m["idx"], "English"], "japanese": full_df.at[m["idx"], "Japanese"]}
            for m in mistakes if isinstance(m, dict)
        ],
        direction_label="日本語 → 英語" if direction == "je" else "英語 → 日本語"
    )
@app.route("/rough_result")
@login_required
def rough_result():
    quiz_rows = session.get("quiz_rows", [])
    score = session.get("score", 0)
    total = len(quiz_rows)
    direction = session.get("quiz_direction", "je")
    quiz_type = session.get("quiz_type", "rough")

    # ミス一覧（復習なら全件、通常なら rough_mistakes）
    mistakes = []
    if quiz_type == "rough_review":
        mistakes = session.get("quiz_rows", [])
    elif quiz_type in ['rough_je', 'rough_ej', 'rough']:
        key = f"rough_{direction}"
        mistakes = session.get("rough_mistakes", {}).get(key, [])

    # 表示用に整形
    mistake_words = []
    for m in mistakes:
        if isinstance(m, dict):
            idx = m["idx"]
        else:
            idx = m
        mistake_words.append({
            "english": full_df.at[idx, "English"],
            "japanese": full_df.at[idx, "Japanese"]
        })

    return render_template(
        "rough_result.html",
        score=score,
        total=total,
        direction_label="日本語 → 英語" if direction == "je" else "英語 → 日本語",
        mistake_words=mistake_words
    )

@app.route("/resume_rough_quiz")
@login_required
def resume_rough_quiz():
    saved = session.get('saved_rough')
    if not saved:
        flash("再開できるざっくりクイズが見つかりませんでした。", "warning")
        return redirect(url_for('rough_menu'))

    session['quiz_rows']      = saved['rows']
    session['index']          = saved['index']
    session['score']          = saved['score']
    session['quiz_direction'] = saved['direction']
    session['quiz_type']      = saved['quiz_type']
    session['rough_mistakes'] = saved.get('mistakes', {'rough_je':[], 'rough_ej':[]})

    return redirect(url_for('rough_quiz'))

@app.route("/exit_rough_quiz")
@login_required
def exit_rough_quiz_to_menu():
    # ざっくりクイズ中でなければメインメニューへ
    quiz_type = session.get("quiz_type", "")
    if not quiz_type.startswith("rough"):
        return redirect(url_for("rough_menu"))

    # セーブ用データを saved_rough に格納
    session['saved_rough'] = {
        'rows':      session.get('quiz_rows', []),
        'index':     session.get('index', 0),
        'score':     session.get('score', 0),
        'direction': session.get('quiz_direction'),
        'quiz_type': quiz_type,
        'mistakes':  session.get('rough_mistakes', {'rough_je':[], 'rough_ej':[]})
    }

    # クイズ進行用キーをクリア
    for key in ['quiz_rows', 'index', 'score', 'quiz_direction', 'quiz_type', 'rough_mistakes']:
        session.pop(key, None)

    return redirect(url_for("rough_menu"))
@app.route("/exit_rough_quiz_to_range")
@login_required
def exit_rough_quiz_to_range():
    # ざっくりクイズ中でなければメニューへ
    if session.get('quiz_type') != 'rough':
        return redirect(url_for('rough_menu'))

    # 保存データを組み立て
    start, end = session.get('rough_range', (None, None))
    if start is None:
        return redirect(url_for('rough_menu'))

    direction = session['quiz_direction']
    range_key = f"{start}-{end}"

    saved = session.setdefault('saved_rough_states', {})
    dir_states = saved.setdefault(direction, {})
    dir_states[range_key] = {
        'rows':    session.get('quiz_rows', []),
        'index':   session.get('index', 0),
        'score':   session.get('score', 0),
        'mistakes': session.get('rough_mistakes', {'rough_je':[], 'rough_ej':[]})
    }
    session['saved_rough_states'] = saved

    # クイズのセッションデータをクリア
    for k in ['quiz_rows','index','score','quiz_direction','quiz_type','rough_mistakes','rough_range']:
        session.pop(k, None)

    return redirect(url_for('rough_range_selector', direction=direction))

@app.route("/resume_rough_quiz_with_range/<direction>/<range_key>")
@login_required
def resume_rough_quiz_with_range(direction, range_key):
    saved = session.get('saved_rough_states', {}).get(direction, {})
    state = saved.get(range_key)
    if not state:
        flash("再開できるざっくりクイズが見つかりませんでした。", "warning")
        return redirect(url_for('rough_range_selector', direction=direction))

    # セッションに戻す
    session['quiz_rows']      = state['rows']
    session['index']          = state['index']
    session['score']          = state['score']
    session['quiz_direction'] = direction
    session['quiz_type']      = 'rough'
    session['rough_mistakes'] = state.get('mistakes', {'rough_je':[], 'rough_ej':[]})
    # 範囲も復元
    start, end = map(int, range_key.split('-'))
    session['rough_range'] = (start, end)

    return redirect(url_for('rough_quiz'))


# 関数名とルートを変更
@app.route("/manage_rough_mistakes", methods=["GET", "POST"])
@login_required
def manage_rough_mistakes():
    if request.method == "POST":
        indices_to_delete = [int(i) for i in request.form.getlist('delete_indices')]
        
        if indices_to_delete:
            global_mistakes = session.get('global_rough_mistakes', [])
            
            updated_mistakes = [
                mistake for mistake in global_mistakes 
                if mistake['idx'] not in indices_to_delete
            ]
            
            session['global_rough_mistakes'] = updated_mistakes
            flash(f"{len(indices_to_delete)}件の単語を復習リストから削除しました。", "success")

        # redirect先を変更
        return redirect(url_for('manage_rough_mistakes'))

    # GETリクエストの処理
    mistake_words = []
    global_mistakes = session.get('global_rough_mistakes', [])
    
    unique_indices = sorted(list(set(m['idx'] for m in global_mistakes)))
    
    for idx in unique_indices:
        mistake_words.append({
            'index': idx,
            'english': full_df.at[idx, 'English'],
            'japanese': full_df.at[idx, 'Japanese']
        })

    # 呼び出すテンプレート名を変更
    return render_template("manage_rough_mistakes.html", mistake_words=mistake_words)

@app.route("/remove_from_review", methods=["POST"])
@login_required
def remove_from_review():
    # フォームから削除対象の単語のIDを取得
    index_to_delete = int(request.form.get('word_index'))

    if index_to_delete is not None:
        # 1. 永続的な復習リストから削除
        global_mistakes = session.get('global_rough_mistakes', [])
        updated_global_mistakes = [
            mistake for mistake in global_mistakes
            if mistake['idx'] != index_to_delete
        ]
        session['global_rough_mistakes'] = updated_global_mistakes

        # 2. 現在進行中の復習クイズリストからも削除
        # (同じセッションで再度表示されるのを防ぐため)
        current_review_rows = session.get('quiz_rows', [])
        updated_review_rows = [
            mistake for mistake in current_review_rows
            if mistake['idx'] != index_to_delete
        ]
        session['quiz_rows'] = updated_review_rows

        flash(f"単語を復習リストから削除しました。", "success")

    # 次の問題へリダイレクト
    return redirect(url_for('rough_next_question'))