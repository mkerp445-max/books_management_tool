import os
import sqlite3
import requests
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash

app = Flask(__name__)
app.secret_key = 'book_management_final_key'

DATABASE = 'book_management.db'


# --------------------
# DB接続
# --------------------
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


# --------------------
# 初期化
# --------------------
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
# 外部API
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

    except Exception:
        pass

    return None


# --------------------
# 現在所持数
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
# index
# --------------------
@app.route('/')
def index():
    return render_template('index.html')


# --------------------
# persons
# --------------------
@app.route('/persons', methods=['GET', 'POST'])
def persons():
    conn = get_db()

    if request.method == 'POST':
        name = request.form.get('name', '').strip()

        if not name:
            flash("名前は必須です")
            return redirect(url_for('persons'))

        conn.execute(
            'INSERT INTO persons (name, created_at) VALUES (?, ?)',
            (name, datetime.now().strftime('%Y-%m-%d'))
        )
        conn.commit()

    persons = conn.execute(
        'SELECT * FROM persons WHERE is_active = 1'
    ).fetchall()

    conn.close()
    return render_template('persons.html', persons=persons)


# --------------------
# book get
# --------------------
@app.route('/book/get', methods=['GET', 'POST'])
def get_book():
    conn = get_db()

    if request.method == 'POST':
        person_id = request.form.get('person_id')
        isbn = request.form.get('isbn', '').strip()
        title = request.form.get('title', '').strip()

        if not person_id:
            flash("利用者を選択してください")
            return redirect(url_for('get_book'))

        if not isbn and not title:
            flash("ISBNまたはタイトルを入力してください")
            return redirect(url_for('get_book'))

        # 上限チェック
        if get_book_count(conn, person_id) >= 20:
            flash("上限20冊です")
            return redirect(url_for('get_book'))

        resolved_title = get_book_title(isbn) or title

        if not resolved_title:
            flash("タイトルが取得できません")
            return redirect(url_for('get_book'))

        book_id = f"BK-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"

        conn.execute('''
            INSERT INTO books_history (book_id, person_id, isbn, title, status, timestamp)
            VALUES (?, ?, ?, ?, '取得', ?)
        ''', (
            book_id,
            person_id,
            isbn,
            resolved_title,
            datetime.now().strftime('%Y-%m-%d %H:%M')
        ))

        conn.commit()
        flash("登録しました")

    persons = conn.execute(
        'SELECT * FROM persons WHERE is_active = 1'
    ).fetchall()

    conn.close()
    return render_template('get_book.html', persons=persons)


# --------------------
# dispose
# --------------------
@app.route('/book/dispose/<int:person_id>', methods=['GET', 'POST'])
def dispose_book(person_id):
    conn = get_db()

    person = conn.execute(
        'SELECT * FROM persons WHERE id = ? AND is_active = 1',
        (person_id,)
    ).fetchone()

    if not person:
        flash("利用者が不正です")
        return redirect(url_for('persons'))

    if request.method == 'POST':
        book_id = request.form.get('book_id')

        if not book_id:
            flash("本が未選択です")
            return redirect(url_for('dispose_book', person_id=person_id))

        latest = conn.execute('''
            SELECT status
            FROM books_history
            WHERE book_id = ?
            ORDER BY timestamp DESC
            LIMIT 1
        ''', (book_id,)).fetchone()

        if not latest or latest["status"] != "取得":
            flash("廃棄できない状態です")
            return redirect(url_for('dispose_book', person_id=person_id))

        conn.execute('''
            INSERT INTO books_history (book_id, person_id, title, status, timestamp)
            SELECT book_id, person_id, title, '廃棄', ?
            FROM books_history
            WHERE book_id = ?
            ORDER BY timestamp DESC
            LIMIT 1
        ''', (
            datetime.now().strftime('%Y-%m-%d %H:%M'),
            book_id
        ))

        conn.commit()
        flash("廃棄しました")
        return redirect(url_for('history', person_id=person_id))

    books = conn.execute('''
        SELECT *
        FROM books_history b1
        WHERE person_id = ?
        AND status = '取得'
        AND NOT EXISTS (
            SELECT 1
            FROM books_history b2
            WHERE b2.book_id = b1.book_id
            AND b2.status = '廃棄'
            AND b2.timestamp > b1.timestamp
        )
    ''', (person_id,)).fetchall()

    conn.close()

    return render_template(
        'dispose_book.html',
        books=books,
        person=person
    )


# --------------------
# history
# --------------------
@app.route('/history/<int:person_id>')
def history(person_id):
    conn = get_db()

    person = conn.execute(
        'SELECT * FROM persons WHERE id = ?',
        (person_id,)
    ).fetchone()

    logs = conn.execute('''
        SELECT * FROM books_history
        WHERE person_id = ?
        ORDER BY timestamp DESC
    ''', (person_id,)).fetchall()

    conn.close()

    return render_template('history.html', person=person, logs=logs)

# --- 利用者の削除（論理削除） ---
@app.route('/person/delete/<int:id>')
def delete_person(id):
    conn = get_db()
    try:
        # is_activeを0にして、画面上に出ないようにする
        conn.execute('UPDATE persons SET is_active = 0 WHERE id = ?', (id,))
        conn.commit()
        flash('利用者を削除しました')
    except Exception as e:
        flash(f'削除に失敗しました: {e}')
    finally:
        conn.close()
    return redirect(url_for('persons'))



# --------------------
# run
# --------------------
if __name__ == '__main__':
    app.run(debug=True)