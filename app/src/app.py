import os
import psycopg2
from flask import Flask, jsonify, request, render_template
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

def get_db():
    return psycopg2.connect(
        host=os.environ.get('DB_HOST'),
        user=os.environ.get('DB_USER'),
        password=os.environ.get('DB_PASSWORD'),
        dbname=os.environ.get('DB_NAME'),
        port=os.environ.get('DB_PORT', 5432)
    )


def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(100) NOT NULL,
            email VARCHAR(100) NOT NULL,
            password VARCHAR(100) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    cursor.close()
    conn.close()

init_db()

@app.route('/users', methods=['GET'])
def get_users():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, email, created_at FROM users")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    users = [
        {"id": r[0], "username": r[1], "email": r[2], "created_at": str(r[3])}
        for r in rows
    ]
    return jsonify(users)


@app.route('/users', methods=['POST'])
def create_user():
    data = request.get_json()
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO users (username, email, password) VALUES (%s, %s, %s) RETURNING id",
        (username, email, password)
    )
    user_id = cursor.fetchone()[0]
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"id": user_id, "username": username, "email": email}), 201


@app.route('/')
def index():
    return render_template('index.html')


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
