-- =============================================================================
-- DistriStock Pro — Schéma PostgreSQL initial
-- Exécuté automatiquement au premier démarrage du conteneur database.
-- =============================================================================

-- Table gérée par le microservice Inventaire (catalogue & stocks)
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    uuid UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    sku VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    category VARCHAR(100),
    purchase_price NUMERIC(12, 2) NOT NULL DEFAULT 0,
    sale_price NUMERIC(12, 2) NOT NULL DEFAULT 0,
    quantity_stock INTEGER NOT NULL DEFAULT 0 CHECK (quantity_stock >= 0),
    low_stock_threshold INTEGER NOT NULL DEFAULT 5,
    image_url VARCHAR(512),
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Table gérée par le microservice Ventes (commandes clients)
CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    order_reference VARCHAR(32) NOT NULL UNIQUE,
    customer_name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL,
    total_amount NUMERIC(12, 2) NOT NULL,
    payment_method VARCHAR(32) NOT NULL CHECK (
        payment_method IN ('orange_money', 'wave', 'card', 'cash')
    ),
    order_status VARCHAR(32) NOT NULL DEFAULT 'pending' CHECK (
        order_status IN ('pending', 'validated', 'delivered', 'cancelled')
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(order_status);

-- Données de démonstration (SKU utilisés par la boutique)
INSERT INTO products (sku, name, description, category, purchase_price, sale_price, quantity_stock, low_stock_threshold, image_url, status)
VALUES
    ('DSP-RJ45-001', 'Câble RJ45 Cat6 2m', 'Câble Ethernet blindé haute qualité pour réseau local.', 'Câblage', 3.50, 7.90, 120, 15,
     'https://images.unsplash.com/photo-1544197150-b99a580bb7a8?w=400', 'active'),
    ('DSP-SW-002', 'Switch 8 ports Gigabit', 'Commutateur non managé, idéal pour petit bureau.', 'Réseau', 25.00, 49.00, 30, 5,
     'https://images.unsplash.com/photo-1558494949-ef010cbdcc31?w=400', 'active'),
    ('DSP-USB-003', 'Hub USB-C 4 ports', 'Hub aluminium compact pour laptop.', 'Accessoires', 12.00, 24.90, 8, 10,
     'https://images.unsplash.com/photo-1625948515291-69613efd103f?w=400', 'active'),
    ('DSP-WIFI-004', 'Routeur Wi-Fi 6', 'Routeur dual-band pour la maison.', 'Réseau', 45.00, 89.00, 5, 3,
     'https://images.unsplash.com/photo-1633356122544-f134324a6cee?w=400', 'active')
ON CONFLICT (sku) DO NOTHING;
