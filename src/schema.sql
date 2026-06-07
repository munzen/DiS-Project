-- schema.sql
-- Stock portfolio web app — database schema (PostgreSQL)
-- Run with:  psql -d portfolio_app -f schema.sql

-- Drop existing tables so the script can be re-run cleanly during development.
-- CASCADE also drops dependent objects (e.g. foreign keys pointing here).
DROP TABLE IF EXISTS leaderboard CASCADE;
DROP TABLE IF EXISTS orders      CASCADE;
DROP TABLE IF EXISTS holdings    CASCADE;
DROP TABLE IF EXISTS stock_price CASCADE;
DROP TABLE IF EXISTS stocks      CASCADE;
DROP TABLE IF EXISTS users       CASCADE;


-- USER -----------------------------------------------------------------
CREATE TABLE users (
    user_id  SERIAL        PRIMARY KEY,
    name     VARCHAR(50)   NOT NULL UNIQUE,                -- used as the login name
    password VARCHAR(100)  NOT NULL,                       -- bcrypt hash
    balance  NUMERIC(12,2) NOT NULL DEFAULT 100000.00,     -- cash on hand
    sim_date DATE          NOT NULL DEFAULT '2000-01-03'   -- pinned simulator start (first trading day)
);


-- STOCK ----------------------------------------------------------------
CREATE TABLE stocks (
    ticker       VARCHAR(10)  PRIMARY KEY,
    company_name VARCHAR(200) NOT NULL,
    exchange     VARCHAR(50)  DEFAULT NULL
);


-- STOCK_PRICE ----------------------------------------------------------
-- Weak entity: a price row is identified by its parent stock + the date.
CREATE TABLE stock_price (
    ticker      VARCHAR(10)   NOT NULL REFERENCES stocks(ticker),
    price_date  DATE          NOT NULL,
    close_price NUMERIC(12,2) NOT NULL,
    PRIMARY KEY (ticker, price_date)
);


-- HOLDINGS -------------------------------------------------------------
-- A user's current position in one stock. The two FK columns are the
-- ER relationships ("Has" to USER, the line to STOCK) made concrete.
CREATE TABLE holdings (
    holding_id SERIAL      PRIMARY KEY,
    user_id    INTEGER     NOT NULL REFERENCES users(user_id),
    ticker     VARCHAR(10) NOT NULL REFERENCES stocks(ticker),
    quantity   INTEGER     NOT NULL DEFAULT 0,
    UNIQUE (user_id, ticker)        -- one holding row per user/stock pair
);


-- ORDERS ---------------------------------------------------------------
-- "order" is a reserved SQL word, so the table is named "orders".
CREATE TABLE orders (
    order_id   SERIAL      PRIMARY KEY,
    user_id    INTEGER     NOT NULL REFERENCES users(user_id),
    ticker     VARCHAR(10) NOT NULL REFERENCES stocks(ticker),
    order_date DATE        NOT NULL DEFAULT CURRENT_DATE,
    type       VARCHAR(4)  NOT NULL CHECK (type IN ('buy', 'sell')),
    quantity   INTEGER     NOT NULL CHECK (quantity > 0)
);


-- LEADERBOARD ----------------------------------------------------------
-- Final score (portfolio market value + cash balance) recorded when a
-- user's simulation reaches the end of the dataset. Keyed on the user's
-- login name so the board can be shown without joining back to users.
CREATE TABLE leaderboard (
    entry_id    SERIAL        PRIMARY KEY,
    name        VARCHAR(50)   NOT NULL REFERENCES users(name),
    score       NUMERIC(14,2) NOT NULL,
    recorded_at TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP
);
