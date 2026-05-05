import os
import sqlite3
import requests
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash

app = Flask(__name__)
app.secret_key = 'book_secret_key_999'

DATABASE = 'book_management_v1.db'

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    # 人の管理テーブル
    cur.execute('''
        CREATE TABLE IF NOT EXISTS persons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            is_active INTEGER DEFAULT 1
        )
    ''')
    # 本の履歴テーブル (図書カード)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS books_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER NOT NULL,
            isbn TEXT,
            title TEXT NOT NULL,
            status TEXT NOT NULL, -- '取得' or '廃棄'
            timestamp TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- Google Books API 連携 ---
def get_book_title(isbn):
    try:
        url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
        response = requests.get(url, timeout=5)
        data = response.json()
        if "items" in data:
            return data["items"][0]["volumeInfo"]["title"]
    except Exception as e:
        print(f"API Error: {e}")
    return None

# --- ルーティング ---

@app.route('/')
def index():
    return render_template('index.html')

# 1-(1) 人の管理
@app.route('/persons', methods=['GET', 'POST'])
def persons():
    conn = get_db()
    if request.method == 'POST':
        name = request.form.get('name')
        date = datetime.now().strftime('%Y-%m-%d')
        conn.execute('INSERT INTO persons (name, created_at) VALUES (?, ?)', (name, date))
        conn.commit()
        return redirect(url_for('persons'))
    
    person_list = conn.execute('SELECT * FROM persons WHERE is_active = 1').fetchall()
    conn.close()
    return render_template('persons.html', persons=person_list)

@app.route('/person/delete/<int:id>')
def delete_person(id):
    conn = get_db()
    conn.execute('UPDATE persons SET is_active = 0 WHERE id = ?', (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('persons'))

# 2-(2) 本の管理 - 取得
@app.route('/book/get', methods=['GET', 'POST'])
def get_book():
    conn = get_db()
    if request.method == 'POST':
        person_id = request.form.get('person_id')
        isbn = request.form.get('isbn')
        manual_title = request.form.get('title')
        
        # 20冊制限チェック (取得数 - 廃棄数)
        current_count = conn.execute('''
            SELECT (SELECT COUNT(*) FROM books_history WHERE person_id = ? AND status = '取得') -
                   (SELECT COUNT(*) FROM books_history WHERE person_id = ? AND status = '廃棄')
        ''', (person_id, person_id)).fetchone()[0]

        if current_count >= 20:
            flash('エラー：一人が所有できるのは20冊までです。')
            return redirect(url_for('get_book'))

        # タイトル決定 (API優先、なければ手動)
        title = get_book_title(isbn) if isbn else manual_title
        if not title: title = manual_title

        if title:
            conn.execute('''
                INSERT INTO books_history (person_id, isbn, title, status, timestamp)
                VALUES (?, ?, ?, '取得', ?)
            ''', (person_id, isbn, title, datetime.now().strftime('%Y-%m-%d %H:%M')))
            conn.commit()
            flash(f'「{title}」を取得しました。')
        else:
            flash('エラー：タイトルが見つかりませんでした。')

    person_list = conn.execute('SELECT * FROM persons WHERE is_active = 1').fetchall()
    conn.close()
    return render_template('get_book.html', persons=person_list)

# 2-(2) 本の管理 - 廃棄
@app.route('/book/dispose/<int:person_id>', methods=['GET', 'POST'])
def dispose_book(person_id):
    conn = get_db()
    if request.method == 'POST':
        book_title = request.form.get('title')
        isbn = request.form.get('isbn')
        conn.execute('''
            INSERT INTO books_history (person_id, isbn, title, status, timestamp)
            VALUES (?, ?, ?, '廃棄', ?)
        ''', (person_id, isbn, book_title, datetime.now().strftime('%Y-%m-%d %H:%M')))
        conn.commit()
        return redirect(url_for('history', person_id=person_id))

    # 現在持っている本だけを特定（同じISBNで取得>廃棄になっていないもの）
    current_books = conn.execute('''
        SELECT title, isbn FROM books_history 
        WHERE person_id = ? AND status = '取得'
        GROUP BY isbn, title
        HAVING COUNT(CASE WHEN status='取得' THEN 1 END) > COUNT(CASE WHEN status='廃棄' THEN 1 END)
    ''', (person_id,)).fetchall()
    
    person = conn.execute('SELECT * FROM persons WHERE id = ?', (person_id,)).fetchone()
    conn.close()
    return render_template('dispose_book.html', books=current_books, person=person)

# 図書カード（履歴）表示
@app.route('/history/<int:person_id>')
def history(person_id):
    conn = get_db()
    person = conn.execute('SELECT * FROM persons WHERE id = ?', (person_id,)).fetchone()
    logs = conn.execute('SELECT * FROM books_history WHERE person_id = ? ORDER BY timestamp DESC', (person_id,)).fetchall()
    conn.close()
    return render_template('history.html', person=person, logs=logs)

if __name__ == '__main__':
    app.run(debug=True)