import os
import sqlite3
import requests
import logging
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash

# --------------------
# ログの設定
# --------------------
logging.basicConfig(
    filename='app.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    encoding='utf-8'
)

app = Flask(__name__)
app.secret_key = 'book_management_final_key'

DATABASE = 'book_management.db'

# --------------------
# DB接続・初期化
# --------------------
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS persons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            is_active INTEGER DEFAULT 1
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS books_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id TEXT NOT NULL,
            person_id INTEGER NOT NULL,
            isbn TEXT,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --------------------
# 外部API (Google Books API)
# --------------------
def get_book_title(isbn):
    if not isbn:
        return None
    try:
        url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
        res = requests.get(url, timeout=5)
        res.raise_for_status()
        data = res.json()
        if "items" in data:
            return data["items"][0]["volumeInfo"]["title"]
    except Exception as e:
        logging.error(f"API通信エラー (ISBN:{isbn}): {e}")
        return None
    return None

# --------------------
# 補助関数（所持数計算）
# --------------------
def get_book_count(conn, person_id):
    row = conn.execute('''
        SELECT 
            (SELECT COUNT(*) FROM books_history WHERE person_id = ? AND status = '取得')
            -
            (SELECT COUNT(*) FROM books_history WHERE person_id = ? AND status = '廃棄')
        AS cnt
    ''', (person_id, person_id)).fetchone()
    return row["cnt"] if row else 0

# --------------------
# ルーティング
# --------------------

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/persons', methods=['GET', 'POST'])
def persons():
    conn = get_db()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if name:
            conn.execute('INSERT INTO persons (name, created_at) VALUES (?, ?)',
                         (name, datetime.now().strftime('%Y-%m-%d')))
            conn.commit()
            logging.info(f"新規利用者登録: {name}")
        else:
            flash("名前は必須です")

    persons_data = conn.execute('SELECT * FROM persons WHERE is_active = 1').fetchall()
    display_persons = []
    for p in persons_data:
        p_dict = dict(p)
        p_dict['current_count'] = get_book_count(conn, p['id'])
        display_persons.append(p_dict)
    conn.close()
    return render_template('persons.html', persons=display_persons)

@app.route('/get_book', methods=['GET', 'POST'])
def get_book():
    conn = get_db()
    persons_list = conn.execute('SELECT * FROM persons WHERE is_active = 1').fetchall()

    if request.method == 'POST':
        isbn = request.form.get('isbn', '').strip()
        person_id = request.form.get('person_id')
        title = request.form.get('title', '').strip()

        logging.info(f"登録リクエスト: 利用者ID={person_id}, ISBN={isbn}")

        if not isbn or not person_id:
            flash("ISBNと利用者は必須項目です。")
            conn.close()
            return redirect(url_for('get_book'))

        if not (isbn.isdigit() and len(isbn) == 13):
            logging.warning(f"ISBN形式エラー: {isbn}")
            flash("不正なISBN形式です。13桁の数字で入力してください。")
            conn.close()
            return redirect(url_for('get_book'))

        if get_book_count(conn, person_id) >= 20:
            logging.warning(f"冊数制限超過: 利用者ID={person_id}")
            flash("この利用者はすでに20冊登録しています。")
            conn.close()
            return redirect(url_for('get_book'))
        
        if not title:
            title = get_book_title(isbn) or "タイトル不明"

        try:
            book_id = f"{isbn}_{datetime.now().strftime('%M%S')}"
            conn.execute('''
                INSERT INTO books_history (book_id, person_id, isbn, title, status, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (book_id, person_id, isbn, title, '取得', datetime.now().strftime('%Y-%m-%d %H:%M')))
            conn.commit()
            logging.info(f"登録成功: {title} (ISBN:{isbn})")
            flash(f"「{title}」を登録しました！")
        except sqlite3.Error as e:
            logging.error(f"DB登録エラー: {e}")
            flash("保存に失敗しました。")
            conn.rollback()
        finally:
            conn.close()
        return redirect(url_for('persons'))

    conn.close()
    return render_template('get_book.html', persons=persons_list)

@app.route('/book/dispose/<int:person_id>', methods=['GET', 'POST'])
def dispose_book(person_id):
    conn = get_db()
    person = conn.execute('SELECT * FROM persons WHERE id = ? AND is_active = 1', (person_id,)).fetchone()

    if request.method == 'POST':
        book_id = request.form.get('book_id')
        if book_id:
            conn.execute('''
                INSERT INTO books_history (book_id, person_id, title, status, timestamp)
                SELECT book_id, person_id, title, '廃棄', ?
                FROM books_history WHERE book_id = ? ORDER BY timestamp DESC LIMIT 1
            ''', (datetime.now().strftime('%Y-%m-%d %H:%M'), book_id))
            conn.commit()
            logging.info(f"廃棄処理完了: book_id={book_id}")
            flash("廃棄しました")
            return redirect(url_for('history', person_id=person_id))

    books = conn.execute('''
        SELECT * FROM books_history b1 WHERE person_id = ? AND status = '取得'
        AND NOT EXISTS (
            SELECT 1 FROM books_history b2 WHERE b2.book_id = b1.book_id
            AND b2.status = '廃棄' AND b2.timestamp > b1.timestamp
        )
    ''', (person_id,)).fetchall()
    conn.close()
    return render_template('dispose_book.html', books=books, person=person)

@app.route('/history/<int:person_id>')
def history(person_id):
    conn = get_db()
    person = conn.execute('SELECT * FROM persons WHERE id = ?', (person_id,)).fetchone()
    logs = conn.execute('SELECT * FROM books_history WHERE person_id = ? ORDER BY timestamp DESC', (person_id,)).fetchall()
    conn.close()
    return render_template('history.html', person=person, logs=logs)

@app.route('/person/delete/<int:id>')
def delete_person(id):
    conn = get_db()
    try:
        conn.execute('UPDATE persons SET is_active = 0 WHERE id = ?', (id,))
        conn.commit()
        logging.info(f"利用者論理削除: ID={id}")
        flash('利用者を削除しました')
    except Exception as e:
        logging.error(f"利用者削除エラー: {e}")
    finally:
        conn.close()
    return redirect(url_for('persons'))

# 確認が終わったら、この部分は消してOKです！
# with app.app_context():
#     test_title = get_book_title("9784575239058")
#     print(f"--- テスト結果: {test_title} ---")

if __name__ == '__main__':
    app.run(debug=True)