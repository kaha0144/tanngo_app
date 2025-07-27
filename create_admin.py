from app import app, db, User, generate_password_hash

# --- ここで最初の管理者情報を設定 ---
ADMIN_USERNAME = "admin"
ADMIN_NICKNAME = "管理者"
ADMIN_PASSWORD = "password"  # 必ず後で変更してください！
# ------------------------------------

with app.app_context():
    # ユーザーが既に存在するかチェック
    existing_user = User.query.filter_by(username=ADMIN_USERNAME).first()
    if existing_user:
        print(f"ユーザー '{ADMIN_USERNAME}' は既に存在します。")
    else:
        # pbkdf2:sha256方式でパスワードをハッシュ化
        hashed_password = generate_password_hash(
            ADMIN_PASSWORD,
            method="pbkdf2:sha256"
        )
        
        # is_admin=Trueで管理者ユーザーを作成
        admin_user = User(
            username=ADMIN_USERNAME,
            nickname=ADMIN_NICKNAME,
            password=hashed_password,
            is_admin=True
        )
        
        db.session.add(admin_user)
        db.session.commit()
        print(f"管理者ユーザー '{ADMIN_USERNAME}' が正常に作成されました。")