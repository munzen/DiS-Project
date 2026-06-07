from functools import wraps
from flask import Blueprint, render_template, session, redirect, url_for, jsonify, request
from .db import get_db

bp = Blueprint('views', __name__)

# Simulation is pinned to begin at the turn of the millennium. The price data
# reaches further back, but the user can never start (or step) before this.

SIM_MIN_DATE = '2000-01-03'


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login_page'))
        return f(*args, **kwargs)
    return decorated


def portfolio_snapshot(cur, uid, sim_date):
    """Current holdings priced at sim_date, plus their total market value.

    Returns (net_worth, holdings) where holdings is a list of dicts ready to
    serialise - lets the client patch the dashboard without a page reload."""
    cur.execute(
        '''
        SELECT s.ticker, s.company_name, h.quantity,
               sp.close_price,
               h.quantity * sp.close_price AS value
        FROM holdings h
        JOIN stocks s ON s.ticker = h.ticker
        LEFT JOIN stock_price sp ON sp.ticker = h.ticker AND sp.price_date = %s
        WHERE h.user_id = %s
        ORDER BY s.ticker
        ''',
        (sim_date, uid),
    )
    rows = cur.fetchall()
    net_worth = round(sum(float(value) for *_, value in rows if value is not None), 2)
    holdings = [
        {
            'ticker': ticker,
            'company': company,
            'quantity': qty,
            'price': float(price) if price is not None else None,
            'value': float(value) if value is not None else None,
        }
        for ticker, company, qty, price, value in rows
    ]
    return net_worth, holdings


@bp.get('/')
@login_required
def dashboard():
    db = get_db()
    cur = db.cursor()
    uid = session['user_id']

    balance = float(session.get('balance', 100000.00))
    sim_date = session.get('sim_date')

    # net worth = market value of all holdings at sim_date's close prices
    net_worth, holdings = portfolio_snapshot(cur, uid, sim_date)

    cur.execute('SELECT name FROM users WHERE user_id = %s', (uid,))
    row = cur.fetchone()
    cur.close()
    if row is None:
        # stale session: user_id no longer in DB (e.g. after DB recreate)
        session.clear()
        return redirect(url_for('auth.login_page'))
    user_name = row[0]

    return render_template(
        'dashboard.html',
        balance=balance,
        net_worth=net_worth,
        sim_date=sim_date,
        holdings=holdings,
        user_name=user_name,
    )


# How far each "advance" button jumps, as a PostgreSQL interval.
ADVANCE_STEPS = {
    'day': '1 day',
    'week': '1 week',
    'month': '1 month',
    'year': '1 year',
}


@bp.post('/advance-day')
@login_required
def advance_day():
    data = request.get_json(silent=True) or {}
    unit = data.get('unit', 'day')
    if unit != 'end' and unit not in ADVANCE_STEPS:
        return jsonify({'error': 'unit must be one of day, week, month, year, end'}), 400

    sim_date = session.get('sim_date')
    db = get_db()
    cur = db.cursor()

    if unit == 'end':
        # Jump straight to the last trading day in the dataset.
        cur.execute('SELECT MAX(price_date) FROM stock_price')
    elif sim_date is None:
        # Not started yet: jump to the first trading day at/after the pinned start.
        cur.execute(
            "SELECT MIN(price_date) FROM stock_price WHERE price_date >= %s",
            (SIM_MIN_DATE,),
        )
    elif unit == 'day':
        # A single step is the next trading day, skipping weekends/holidays.
        cur.execute(
            'SELECT MIN(price_date) FROM stock_price WHERE price_date > %s',
            (sim_date,),
        )
    else:
        # Week/month/year: first trading day on or after the target calendar date.
        cur.execute(
            "SELECT MIN(price_date) FROM stock_price WHERE price_date >= %s::date + %s::interval",
            (sim_date, ADVANCE_STEPS[unit]),
        )
    next_date = cur.fetchone()[0]

    cur.execute('SELECT MAX(price_date) FROM stock_price')
    max_date = cur.fetchone()[0]
    if max_date is None:
        cur.close()
        return jsonify({'error': 'no more data'}), 400

    # Overshooting the dataset (e.g. +1 year near the end) lands on the final day.
    if next_date is None or next_date >= max_date:
        next_date = max_date

    # The DB column users.sim_date is the source of truth; keep it in step.
    cur.execute(
        'UPDATE users SET sim_date = %s WHERE user_id = %s',
        (next_date, session['user_id']),
    )
    db.commit()
    session['sim_date'] = next_date.isoformat()
    finished = next_date >= max_date
    result = {'sim_date': session['sim_date'], 'finished': finished}

    # Reaching the end of the data finalises the run: record the score once.
    if finished and not session.get('finished'):
        uid = session['user_id']
        cur.execute(
            '''
            SELECT COALESCE(SUM(h.quantity * sp.close_price), 0)
            FROM holdings h
            JOIN stock_price sp ON sp.ticker = h.ticker AND sp.price_date = %s
            WHERE h.user_id = %s
            ''',
            (next_date, uid),
        )
        portfolio_value = float(cur.fetchone()[0])
        score = round(portfolio_value + float(session.get('balance', 100000.00)), 2)

        cur.execute('SELECT name FROM users WHERE user_id = %s', (uid,))
        name = cur.fetchone()[0]
        cur.execute(
            'INSERT INTO leaderboard (name, score) VALUES (%s, %s)',
            (name, score),
        )
        db.commit()
        session['finished'] = True
        result['score'] = score

    # Portfolio snapshot at the new date, so the client can patch the dashboard
    # in place instead of doing a full page reload.
    net_worth, holdings = portfolio_snapshot(cur, session['user_id'], next_date)
    result['balance'] = round(float(session.get('balance', 100000.00)), 2)
    result['net_worth'] = net_worth
    result['holdings'] = holdings

    cur.close()
    return jsonify(result)


@bp.get('/api/leaderboard')
@login_required
def api_leaderboard():
    db = get_db()
    cur = db.cursor()
    cur.execute(
        'SELECT name, score, recorded_at FROM leaderboard ORDER BY score DESC, recorded_at ASC'
    )
    rows = cur.fetchall()
    cur.close()
    return jsonify([
        {'name': name, 'score': float(score), 'recorded_at': recorded_at.isoformat()}
        for name, score, recorded_at in rows
    ])


@bp.post('/trade')
@login_required
def trade():
    data = request.get_json() or {}
    ticker = (data.get('ticker') or '').strip().upper()
    order_type = data.get('type')
    quantity = data.get('quantity')

    if not ticker or order_type not in {'buy', 'sell'} or quantity is None:
        return jsonify({'error': 'ticker, type, and quantity are required'}), 400

    try:
        quantity = int(quantity)
        if quantity <= 0:
            raise ValueError()
    except ValueError:
        return jsonify({'error': 'quantity must be a positive integer'}), 400

    db = get_db()
    cur = db.cursor()
    uid = session['user_id']
    sim_date = session.get('sim_date')
    if sim_date is None: # added due to errors we later found out was due to a stale db. Not needed, but kept for catching odd exceptions
        cur.close()
        return jsonify({'error': 'simulation has not started'}), 400
    balance = float(session.get('balance', 100000.00))

    cur.execute(
        'SELECT close_price FROM stock_price WHERE ticker = %s AND price_date = %s',
        (ticker, sim_date),
    )
    row = cur.fetchone()
    if row is None:
        cur.close()
        return jsonify({'error': 'price data not available for this stock on the current date'}), 404

    price = float(row[0])
    total = price * quantity

    if order_type == 'buy':
        if total > balance:
            cur.close()
            return jsonify({'error': 'insufficient balance'}), 400
        cur.execute(
            '''
            INSERT INTO holdings (user_id, ticker, quantity)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, ticker)
            DO UPDATE SET quantity = holdings.quantity + EXCLUDED.quantity
            ''',
            (uid, ticker, quantity),
        )
        new_balance = balance - total
    else:
        cur.execute(
            'SELECT quantity FROM holdings WHERE user_id = %s AND ticker = %s',
            (uid, ticker),
        )
        row = cur.fetchone()
        if row is None or row[0] < quantity:
            cur.close()
            return jsonify({'error': 'not enough shares to sell'}), 400
        remaining = row[0] - quantity
        if remaining == 0:
            cur.execute(
                'DELETE FROM holdings WHERE user_id = %s AND ticker = %s',
                (uid, ticker),
            )
        else:
            cur.execute(
                'UPDATE holdings SET quantity = %s WHERE user_id = %s AND ticker = %s',
                (remaining, uid, ticker),
            )
        new_balance = balance + total

    cur.execute(
        'INSERT INTO orders (user_id, ticker, order_date, type, quantity) VALUES (%s, %s, %s, %s, %s)',
        (uid, ticker, sim_date, order_type, quantity),
    )
    session['balance'] = round(new_balance, 2)

    # Snapshot the updated portfolio so the client can refresh holdings + value
    # in place, without a page reload.
    net_worth, holdings = portfolio_snapshot(cur, uid, sim_date)
    db.commit()
    cur.close()

    return jsonify({
        'balance': round(new_balance, 2),
        'net_worth': net_worth,
        'holdings': holdings,
        'price': round(price, 2),
        'quantity': quantity,
        'ticker': ticker,
        'type': order_type,
        'message': f'{order_type.capitalize()} order executed for {quantity} shares of {ticker} at ${price:.2f}',
    })


@bp.get('/api/stocks')
@login_required
def api_stocks():
    db = get_db()
    cur = db.cursor()
    sim_date = session.get('sim_date')
    if not sim_date:
        cur.close()
        return jsonify([])
    # Only list stocks that actually trade on the current sim date - a stock with
    # no price row for that day isn't listed yet (or has been delisted).
    cur.execute(
        '''
        SELECT s.ticker, s.company_name
        FROM stocks s
        JOIN stock_price sp ON sp.ticker = s.ticker AND sp.price_date = %s
        ORDER BY s.ticker
        ''',
        (sim_date,),
    )
    rows = cur.fetchall()
    cur.close()
    return jsonify([{'ticker': t, 'name': n} for t, n in rows])


@bp.get('/api/stock-history/<ticker>')
@login_required
def api_stock_history(ticker):
    db = get_db()
    cur = db.cursor()
    sim_date = session.get('sim_date')
    if not sim_date:
        cur.close()
        return jsonify({'error': 'sim not started'}), 400

    cur.execute(
        '''
        SELECT price_date, close_price
        FROM stock_price
        WHERE ticker = %s AND price_date <= %s
        ORDER BY price_date DESC
        LIMIT 60
        ''',
        (ticker, sim_date),
    )
    rows = cur.fetchall()
    cur.close()
    rows.reverse()
    return jsonify([
        {'date': row[0].isoformat(), 'price': float(row[1])}
        for row in rows
    ])


@bp.get('/api/portfolio-history')
@login_required
def api_portfolio_history():
    db = get_db()
    cur = db.cursor()
    uid = session['user_id']
    sim_date = session.get('sim_date')
    sim_start_date = session.get('sim_start_date')
    if not sim_date:
        cur.close()
        return jsonify({'error': 'sim not started'}), 400

    cur.execute(
        'SELECT ticker, quantity FROM holdings WHERE user_id = %s',
        (uid,),
    )
    holdings = cur.fetchall()
    if not holdings:
        cur.close()
        return jsonify([])

    tickers = [row[0] for row in holdings]
    quantities = {row[0]: row[1] for row in holdings}

    cur.execute(
        '''
        SELECT price_date, ticker, close_price
        FROM stock_price
        WHERE ticker = ANY(%s) AND price_date >= %s AND price_date <= %s
        ORDER BY price_date ASC, ticker ASC
        ''',
        (tickers, sim_start_date, sim_date),
    )
    rows = cur.fetchall()
    cur.close()

    values_by_date = {}
    for price_date, ticker, close_price in rows:
        values_by_date.setdefault(price_date.isoformat(), 0)
        values_by_date[price_date.isoformat()] += float(close_price) * quantities[ticker]

    return jsonify([
        {'date': date, 'price': round(price, 2)}
        for date, price in values_by_date.items()
    ])


@bp.get('/api/stock-price/<ticker>')
@login_required
def api_stock_price(ticker):
    db = get_db()
    cur = db.cursor()
    uid = session['user_id']
    sim_date = session.get('sim_date')
    if not sim_date:
        cur.close()
        return jsonify({'error': 'sim not started'}), 400
    cur.execute(
        'SELECT close_price FROM stock_price WHERE ticker = %s AND price_date = %s',
        (ticker, sim_date),
    )
    row = cur.fetchone()
    cur.close()
    if row is None:
        return jsonify({'error': 'no price for this date'}), 404
    return jsonify({'ticker': ticker, 'price': float(row[0]), 'date': sim_date})
