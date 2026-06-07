import bcrypt
from flask import Blueprint, request, jsonify, session, render_template, redirect, url_for
from .db import get_db
from .views import SIM_MIN_DATE

bp = Blueprint('auth', __name__)


def top_scores(limit=20):
    """Top leaderboard entries for display on the public auth pages."""
    db = get_db()
    cur = db.cursor()
    cur.execute(
        'SELECT name, score FROM leaderboard ORDER BY score DESC, recorded_at ASC LIMIT %s',
        (limit,),
    )
    rows = cur.fetchall()
    cur.close()
    return [{'name': name, 'score': float(score)} for name, score in rows]


@bp.get('/login')
def login_page():
    return render_template('login.html', leaderboard=top_scores())


@bp.get('/register')
def register_page():
    return render_template('register.html', leaderboard=top_scores())


@bp.post('/register')
def register():
    data = request.get_json()
    name = data['name']
    password = data['password']
    password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute(
            'INSERT INTO users (name, password) VALUES (%s, %s) RETURNING user_id, balance, sim_date',
            (name, password_hash)
        )
        user_id, balance, sim_date = cur.fetchone()
        db.commit()
    except Exception as exc:
        db.rollback()
        cur.close()
        return jsonify({'error': str(exc)}), 400
    cur.close()
    session['user_id'] = user_id
    session['balance'] = float(balance)
    # The DB seeds sim_date to the pinned start (see users.sim_date default), so
    # Fixed such that the account is ready to trade immediately - no separate "start" step.
    session['sim_date'] = sim_date.isoformat()
    session['sim_start_date'] = sim_date.isoformat()
    return jsonify({'user_id': user_id}), 201


def has_finished(cur, name):
    """A user is "finished" once they have a recorded leaderboard score;
    finished accounts are locked out of further play."""
    cur.execute('SELECT 1 FROM leaderboard WHERE name = %s LIMIT 1', (name,))
    return cur.fetchone() is not None


@bp.post('/login')
def login():
    data = request.get_json()
    name = data['name']
    password = data['password']
    db = get_db()
    cur = db.cursor()
    cur.execute(
        'SELECT user_id, password, balance, sim_date FROM users WHERE name = %s',
        (name,)
    )
    row = cur.fetchone()
    if row is None:
        cur.close()
        return jsonify({'error': 'invalid credentials'}), 401
    user_id, password_hash, balance, sim_date = row
    if not bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8')):
        cur.close()
        return jsonify({'error': 'invalid credentials'}), 401
    if has_finished(cur, name):
        cur.close()
        return jsonify({'error': 'This account has finished the simulation and is locked.'}), 403
    cur.close()
    session['user_id'] = user_id
    session['balance'] = float(balance)
    # sim_date lives in the DB (users.sim_date); rehydrate the session from it.
    session['sim_date'] = sim_date.isoformat()
    session['sim_start_date'] = SIM_MIN_DATE
    return jsonify({'user_id': user_id}), 200


@bp.post('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login_page'))
