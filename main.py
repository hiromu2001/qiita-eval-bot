import os
import requests
import sqlite3
from fastapi import FastAPI, Query
from dotenv import load_dotenv
import openai
import re
from datetime import datetime

load_dotenv()

# 環境変数
QIITA_API_TOKEN = os.getenv("QIITA_API_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = openai.OpenAI(api_key=OPENAI_API_KEY)

app = FastAPI()

# データベース初期化
def init_db():
    conn = sqlite3.connect("evaluation.db")
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS evaluations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user TEXT,
        article_id TEXT,
        title TEXT,
        score INTEGER,
        review TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    conn.commit()
    conn.close()

init_db()

# Qiita APIから記事取得
def get_qiita_article(article_id):
    headers = {"Authorization": f"Bearer {QIITA_API_TOKEN}"}
    url = f"https://qiita.com/api/v2/items/{article_id}"
    response = requests.get(url, headers=headers)
    print(f"[DEBUG] Qiita API Response Status: {response.status_code}")
    if response.status_code != 200:
        return None
    return response.json()

# OpenAIで記事を評価
def evaluate_article_with_openai(article, past_reviews=None):
    tags = ', '.join([tag['name'] for tag in article.get('tags', [])])
    lgtm_count = article.get('likes_count', 0)
    comments_count = article.get('comments_count', 0)
    body_length = len(article.get('body', ''))
    title = article.get('title', '')
    body = article.get('body', '')

    # 振り返りコメント生成
    if past_reviews:
        history_prompt = "【これまでの評価履歴（最新→過去）】\n"
        for idx, (score, review) in enumerate(reversed(past_reviews), 1):
            history_prompt += f"・{idx}回前の評価: スコア {score}点, 要約: {review[:100]}...\n"
        history_prompt += "今回の評価ではこれまでの履歴と比較して成長点や改善点を含めてコメントしてください。"
    else:
        history_prompt = "今回が初めての評価なので、振り返りコメントはありません。"

    print(f"[DEBUG] 振り返りプロンプト: {history_prompt}")

    prompt = f"""
以下のQiita記事について100点満点で評価し、理由を添えてください。さらに、以下の評価ポイントに従ってください。

【評価ポイント】
- 読みやすさ（初心者にもわかりやすいか）
- 論理性（見出し構造、流れ）
- 情報の網羅性
- 実用性（役立つ情報かどうか）
- 人気度（LGTM数、コメント数）
- 説明の深さ（背景や仕組みの理解を助ける内容があるか）
- 独自性（他の記事にはない工夫やアイデアがあるか）

また、以下の3つのルールが守られているか必ず確認し、**守られていない場合は必ず指摘してください**:
- **記事タイトルに「何のために」「何を解決したいか」の目的意識が示されているか**
- **記事のタグが3つ以上設定されているか**
- **マークダウン形式で正しく記載されているか**

【タイトル】
{title}

【本文】
{body}

【補足データ】
- 文字数: {body_length}文字
- LGTM数: {lgtm_count}
- コメント数: {comments_count}
- タグ: {tags}

{history_prompt}

評価結果は以下の形式で返してください。
・点数（100点満点）
・理由（箇条書きで3つ以上）
・改善点（箇条書きで3つ以上）
・振り返りコメント（過去の評価がある場合は「前回と比較して…」のように、過去データと比較したコメントを含めてください。過去の評価がない場合は「今回が初めての評価なので、振り返りコメントはありません」と記載してください。）
"""

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1000,
        temperature=0.7
    )

    result = response.choices[0].message.content
    cleaned_result = result.replace("\n", " ").replace("  ", " ").strip()
    print(f"[DEBUG] GPT評価結果: {cleaned_result}")
    return cleaned_result

# 評価API
@app.get("/evaluate/{article_id}")
def evaluate(article_id: str, user: str = Query(..., description="評価を行うユーザー（プルダウンから選択予定）")):
    try:
        conn = sqlite3.connect("evaluation.db")
        c = conn.cursor()

        # ユーザー別の過去評価履歴取得
        c.execute("SELECT score, review FROM evaluations WHERE user = ? ORDER BY created_at", (user,))
        past_reviews = c.fetchall()
        print(f"[DEBUG] ユーザー: {user} の過去評価履歴: {past_reviews}")

        # 記事取得
        article = get_qiita_article(article_id)
        if not article:
            return {"error": "Qiita記事が取得できませんでした。IDを確認してください。"}

        # 評価実施
        evaluation = evaluate_article_with_openai(article, past_reviews)

        # スコア抽出
        match = re.search(r"(\d{1,3})点", evaluation)
        score = int(match.group(1)) if match else None
        print(f"[DEBUG] 抽出されたスコア: {score}")

        # データベース保存
        c.execute("""
            INSERT INTO evaluations (user, article_id, title, score, review)
            VALUES (?, ?, ?, ?, ?)
        """, (user, article.get('id'), article.get('title'), score, evaluation))
        conn.commit()
        conn.close()

        return {"user": user, "score": score, "review": evaluation}

    except Exception as e:
        print(f"[ERROR] {e}")
        return {"error": "予期しないエラーが発生しました。"}

# ユーザー別履歴取得API
@app.get("/history/{user}")
def get_user_history(user: str):
    try:
        conn = sqlite3.connect("evaluation.db")
        c = conn.cursor()
        c.execute("""
            SELECT article_id, title, score, created_at FROM evaluations
            WHERE user = ?
            ORDER BY created_at DESC
        """, (user,))
        rows = c.fetchall()
        conn.close()

        return {"user": user, "history": [{"article_id": r[0], "title": r[1], "score": r[2], "created_at": r[3]} for r in rows]}
    except Exception as e:
        print(f"[ERROR] {e}")
        return {"error": "履歴取得時にエラーが発生しました。"}
