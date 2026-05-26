-- schema.sql
-- Stock portfolio web app — database schema (PostgreSQL)
-- Run with:  psql -d portfolio_app -f schema.sql

-- Drop existing tables so the script can be re-run cleanly during development.
-- CASCADE also drops dependent objects (e.g. foreign keys pointing here).
DROP TABLE IF EXISTS orders      CASCADE;
DROP TABLE IF EXISTS holdings    CASCADE;
DROP TABLE IF EXISTS stock_price CASCADE;
DROP TABLE IF EXISTS stocks      CASCADE;
DROP TABLE IF EXISTS users       CASCADE;


-- USER -----------------------------------------------------------------
CREATE TABLE users (
    user_id  SERIAL       PRIMARY KEY,
    name     VARCHAR(50)  NOT NULL UNIQUE,   -- used as the login name
    password VARCHAR(100) NOT NULL           -- plaintext: no real traffic intended
);


-- STOCK ----------------------------------------------------------------
CREATE TABLE stocks (
    ticker       VARCHAR(10)  PRIMARY KEY,
    company_name VARCHAR(100) NOT NULL
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
