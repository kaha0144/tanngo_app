import os
from dotenv import load_dotenv
from supabase import create_client, Client

# .envファイルを読み込む
load_dotenv()

# Supabaseクライアントを初期化
# ★★★ service_roleキーを使うのが重要 ★★★
url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_SERVICE_KEY")
supabase: Client = create_client(url, key)

# --- 管理者情報を設定 ---
ADMIN_EMAIL = "admin"  # あなたのメールアドレス
ADMIN_PASSWORD = "pass"
ADMIN_NICKNAME = "管理者"

def create_initial_admin():
    try:
        # 1. Authにユーザーを登録する（管理者権限で）
        # email_confirm=Trueにすると、メール認証をスキップして即時有効になる
        response = supabase.auth.admin.create_user({
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD,
            "email_confirm": True,
        })
        new_user = response.user
        print(f"✅ Authユーザーを作成しました: {new_user.email}")

        # 2. psgr (profiles) テーブルに情報を登録する
        profile_data = {
            "id": new_user.id,
            "nickname": ADMIN_NICKNAME,
            "is_admin": True  # is_adminをTrueに設定
        }
        data, count = supabase.from_('psgr').insert(profile_data).execute()
        print(f"✅ プロフィールを作成しました: {data[1][0]['nickname']}")

    except Exception as e:
        print(f"❌ エラーが発生しました: {e}")

# スクリプトを実行
if __name__ == "__main__":
    create_initial_admin()