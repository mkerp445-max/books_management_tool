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
app.secret_key = 'book_management_normalized_key'

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
    # 1. 利用者テーブル
    conn.execute('''
        CREATE TABLE IF NOT EXISTS persons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            is_active INTEGER DEFAULT 1
        )
    ''')
    # 2. 現在の所有本テーブル (今、誰が何を持っているか)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS owned_books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER NOT NULL,
            isbn TEXT NOT NULL,
            title TEXT NOT NULL,
            added_at TEXT NOT NULL,
            FOREIGN KEY (person_id) REFERENCES persons (id)
        )
    ''')
    # 3. 全履歴テーブル (過去のすべての動き)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS books_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER NOT NULL,
            isbn TEXT,
            title TEXT,
            status TEXT NOT NULL, -- '取得' または '廃棄'
            timestamp TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

# 起動時にDBを初期化
if not os.path.exists(DATABASE):
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
    # owned_booksテーブルを数えるだけなので非常に正確です
    row = conn.execute('SELECT COUNT(*) as cnt FROM owned_books WHERE person_id = ?', (person_id,)).fetchone()
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
@app.route('/get_book', methods=['GET', 'POST'])
def get_book():
    conn = get_db()
    persons_list = conn.execute('SELECT * FROM persons WHERE is_active = 1').fetchall()

    if request.method == 'POST':
        isbn = request.form.get('isbn', '').strip()
        person_id = request.form.get('person_id')
        title = request.form.get('title', '').strip()
        now_dt = datetime.now()
        now_str = now_dt.strftime('%Y-%m-%d %H:%M:%S') # 秒まで記録

        # --- 【修正ポイント：短時間の連打チェック】 ---
        # 履歴テーブルから、この人が最後に登録した同じISBNのレコードを取得
        last_entry = conn.execute('''
            SELECT timestamp FROM books_history 
            WHERE person_id = ? AND isbn = ? AND status = '取得'
            ORDER BY timestamp DESC LIMIT 1
        ''', (person_id, isbn)).fetchone()

        if last_entry:
            # 最後に登録した時間と今の時間を比較
            last_time = datetime.strptime(last_entry['timestamp'], '%Y-%m-%d %H:%M:%S')
            time_diff = (now_dt - last_time).total_seconds()

            if time_diff < 10:  # 10秒以内の再登録は連打とみなす
                flash("連打を検知しました。少し時間を置いてから再度登録してください。")
                conn.close()
                return redirect(url_for('get_book'))
        # --------------------------------------------

        if get_book_count(conn, person_id) >= 20:
            flash("この利用者はすでに20冊登録しています。")
            conn.close()
            return redirect(url_for('get_book'))
        
        if not title:
            title = get_book_title(isbn) or "タイトル不明"

        try:
            conn.execute('''
                INSERT INTO owned_books (person_id, isbn, title, added_at)
                VALUES (?, ?, ?, ?)
            ''', (person_id, isbn, title, now_str))
            
            conn.execute('''
                INSERT INTO books_history (person_id, isbn, title, status, timestamp)
                VALUES (?, ?, ?, '取得', ?)
            ''', (person_id, isbn, title, now_str))
            
            conn.commit()
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
    person = conn.execute('SELECT * FROM persons WHERE id = ?', (person_id,)).fetchone()

    if request.method == 'POST':
        owned_id = request.form.get('owned_id') # owned_booksテーブルのID
        if owned_id:
            # 本の情報を特定
            book = conn.execute('SELECT * FROM owned_books WHERE id = ?', (owned_id,)).fetchone()
            if book:
                now = datetime.now().strftime('%Y-%m-%d %H:%M')
                # 1. 所有テーブルから削除
                conn.execute('DELETE FROM owned_books WHERE id = ?', (owned_id,))
                # 2. 履歴テーブルに「廃棄」を記録
                conn.execute('''
                    INSERT INTO books_history (person_id, isbn, title, status, timestamp)
                    VALUES (?, ?, ?, '廃棄', ?)
                ''', (person_id, book['isbn'], book['title'], now))
                
                conn.commit()
                logging.info(f"廃棄成功: {book['title']} (利用者ID:{person_id})")
                flash(f"「{book['title']}」を廃棄しました")
            
            conn.close()
            return redirect(url_for('history', person_id=person_id))

    # GET時: owned_booksから現在持っている本だけを表示
    books = conn.execute('SELECT * FROM owned_books WHERE person_id = ?', (person_id,)).fetchall()
    conn.close()
    return render_template('dispose_book.html', books=books, person=person)

@app.route('/history/<int:person_id>')
def history(person_id):
    conn = get_db()
    # 利用者情報
    person = conn.execute('SELECT * FROM persons WHERE id = ?', (person_id,)).fetchone()
    
    # 1. 現在所有している本の一覧 (owned_booksテーブルから)
    owned_list = conn.execute('SELECT * FROM owned_books WHERE person_id = ? ORDER BY added_at DESC', (person_id,)).fetchall()
    
    # 2. 全履歴 (books_historyテーブルから)
    logs = conn.execute('SELECT * FROM books_history WHERE person_id = ? ORDER BY timestamp DESC', (person_id,)).fetchall()
    
    conn.close()
    return render_template('history.html', person=person, owned_list=owned_list, logs=logs)

@app.route('/person/delete/<int:id>')
def delete_person(id):
    conn = get_db()
    conn.execute('UPDATE persons SET is_active = 0 WHERE id = ?', (id,))
    conn.commit()
    conn.close()
    flash('利用者を削除しました')
    return redirect(url_for('persons'))

if __name__ == '__main__':
    app.run(debug=True)