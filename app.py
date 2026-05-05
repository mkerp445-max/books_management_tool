import os
import sqlite3
import requests
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash

app = Flask(__name__)
app.secret_key = 'book_secret_key_pro'

DATABASE = 'book_management_v2.db'

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    # 人の管理
    cur.execute('''
        CREATE TABLE IF NOT EXISTS persons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            is_active INTEGER DEFAULT 1
        )
    ''')
    # 本の管理（ここを強化！）
    # book_idを設け、どの「1冊」かを特定できるように変更
    cur.execute('''
        CREATE TABLE IF NOT EXISTS books_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id TEXT NOT NULL, -- 取得時に生成する個別のID
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

# --- Google Books API 連携（例外処理を強化） ---
def get_book_title(isbn):
    try:
        url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
        response = requests.get(url, timeout=5)
        response.raise_for_status() # 400系、500系エラーがあれば例外へ
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

@app.route('/persons', methods=['GET', 'POST'])
def persons():
    conn = get_db()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        # 【バリデーション】空白チェック
        if not name:
            flash('名前を入力してください。')
            return redirect(url_for('persons'))
        
        try:
            date = datetime.now().strftime('%Y-%m-%d')
            conn.execute('INSERT INTO persons (name, created_at) VALUES (?, ?)', (name, date))
            conn.commit()
        except sqlite3.Error as e:
            flash(f'データベースエラーが発生しました: {e}')
        finally:
            conn.close()
        return redirect(url_for('persons'))
    
    person_list = conn.execute('SELECT * FROM persons WHERE is_active = 1').fetchall()
    conn.close()
    return render_template('persons.html', persons=person_list)

@app.route('/book/get', methods=['GET', 'POST'])
def get_book():
    conn = get_db()
    if request.method == 'POST':
        person_id = request.form.get('person_id')
        isbn = request.form.get('isbn', '').strip()
        manual_title = request.form.get('title', '').strip()
        
        # 【バリデーション】入力チェック
        if not person_id:
            flash('利用者を選択してください。')
        elif not isbn and not manual_title:
            flash('ISBNまたはタイトルのどちらかは入力してください。')
        else:
            # 20冊制限チェック
            current_count = conn.execute('''
                SELECT (SELECT COUNT(*) FROM books_history WHERE person_id = ? AND status = '取得') -
                       (SELECT COUNT(*) FROM books_history WHERE person_id = ? AND status = '廃棄')
            ''', (person_id, person_id)).fetchone()[0]

            if current_count >= 20:
                flash('エラー：一人が所有できるのは20冊までです。')
            else:
                title = get_book_title(isbn) if isbn else manual_title
                if not title: title = manual_title

                if title:
                    # 【本に個別IDを付与】
                    # 簡易的にタイムスタンプベースのIDを生成
                    book_id = f"BK-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
                    conn.execute('''
                        INSERT INTO books_history (book_id, person_id, isbn, title, status, timestamp)
                        VALUES (?, ?, ?, ?, '取得', ?)
                    ''', (book_id, person_id, isbn, title, datetime.now().strftime('%Y-%m-%d %H:%M')))
                    conn.commit()
                    flash(f'「{title}」を取得しました。')
                else:
                    flash('タイトルが取得できませんでした。手入力してください。')

    person_list = conn.execute('SELECT * FROM persons WHERE is_active = 1').fetchall()
    conn.close()
    return render_template('get_book.html', persons=person_list)

@app.route('/book/dispose/<int:person_id>', methods=['GET', 'POST'])
def dispose_book(person_id):
    conn = get_db()
    if request.method == 'POST':
        # 本ごとのユニークIDを受け取る
        book_id = request.form.get('book_id')
        title = request.form.get('title')
        if book_id:
            conn.execute('''
                INSERT INTO books_history (book_id, person_id, title, status, timestamp)
                VALUES (?, ?, ?, '廃棄', ?)
            ''', (book_id, person_id, title, datetime.now().strftime('%Y-%m-%d %H:%M')))
            conn.commit()
            flash(f'「{title}」を廃棄として記録しました。')
        return redirect(url_for('history', person_id=person_id))

    # 【廃棄ロジック強化】book_id単位で「まだ廃棄されていない」本を抽出
    current_books = conn.execute('''
        SELECT title, book_id, isbn FROM books_history 
        WHERE person_id = ? AND status = '取得'
        AND book_id NOT IN (
            SELECT book_id FROM books_history WHERE person_id = ? AND status = '廃棄'
        )
    ''', (person_id, person_id)).fetchall()
    
    person = conn.execute('SELECT * FROM persons WHERE id = ?', (person_id,)).fetchone()
    conn.close()
    return render_template('dispose_book.html', books=current_books, person=person)

# historyなどは前のままでOKですが、book_idベースで動くようになります
@app.route('/history/<int:person_id>')
def history(person_id):
    conn = get_db()
    person = conn.execute('SELECT * FROM persons WHERE id = ?', (person_id,)).fetchone()
    logs = conn.execute('SELECT * FROM books_history WHERE person_id = ? ORDER BY timestamp DESC', (person_id,)).fetchall()
    conn.close()
    return render_template('history.html', person=person, logs=logs)

# 以下略（delete_personなど）