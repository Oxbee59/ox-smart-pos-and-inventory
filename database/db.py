import os
import sqlite3
import psycopg2
from psycopg2 import pool, sql, OperationalError
import time
from contextlib import contextmanager

# ---------- Ensure folders exist ----------
DB_DIR = "database"
RECEIPTS_DIR = "receipts"
REPORTS_DIR = "reports"

os.makedirs(DB_DIR, exist_ok=True)
os.makedirs(RECEIPTS_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

# ---------- DATABASE PATHS (for SQLite) ----------
DB_PATH = os.path.join(DB_DIR, "retail.db")
AUTH_DB_PATH = os.path.join(DB_DIR, "auth.db")

# ---------- Check if we are on Render (PostgreSQL) ----------
DATABASE_URL = os.getenv("DATABASE_URL")
USE_POSTGRES = DATABASE_URL is not None

# ---------- Connection Pool (for PostgreSQL) ----------
connection_pool = None
MAX_RETRIES = 3
RETRY_DELAY = 1  # seconds

if USE_POSTGRES:
    print("Using PostgreSQL (Supabase) with connection pooling")
    
    # Ensure SSL is required
    if "sslmode" not in DATABASE_URL:
        if "?" in DATABASE_URL:
            DATABASE_URL += "&sslmode=require"
        else:
            DATABASE_URL += "?sslmode=require"
    
    # Add timeout and keepalive parameters
    if "connect_timeout" not in DATABASE_URL:
        DATABASE_URL += "&connect_timeout=10"
    
    print(f"PostgreSQL URL configured (SSL required)")

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS products (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        brand TEXT,
        cost_price REAL,
        selling_price REAL,
        stock INTEGER,
        category TEXT,
        discount REAL DEFAULT 0,
        base_unit TEXT DEFAULT 'piece'
    );

    CREATE TABLE IF NOT EXISTS sales (
        id SERIAL PRIMARY KEY,
        subtotal REAL DEFAULT 0,
        discount REAL DEFAULT 0,
        total REAL,
        profit REAL,
        date TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        payment_method VARCHAR(50) DEFAULT 'cash',
        cheque_number VARCHAR(100),
        user_id INTEGER REFERENCES users(id)   -- <-- added user_id
    );

    CREATE TABLE IF NOT EXISTS deleted_products (
        id SERIAL PRIMARY KEY,
        name TEXT,
        brand TEXT,
        cost_price REAL,
        selling_price REAL,
        stock INTEGER,
        category TEXT,
        discount REAL DEFAULT 0,
        action TEXT DEFAULT 'deleted',
        source TEXT DEFAULT 'product',
        deleted_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        batch_id INTEGER,
        batch_quantity INTEGER,
        batch_remaining INTEGER,
        product_id INTEGER
    );

    CREATE TABLE IF NOT EXISTS purchases (
        id SERIAL PRIMARY KEY,
        product_name TEXT NOT NULL,
        brand TEXT,
        category TEXT,
        quantity INTEGER,
        cost_price REAL,
        discount REAL DEFAULT 0,
        total REAL,
        selling_price REAL,
        date TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        remaining_stock INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS purchase_batches (
        id SERIAL PRIMARY KEY,
        product_id INTEGER,
        quantity INTEGER,
        remaining_quantity INTEGER,
        cost_price REAL,
        discount REAL,
        selling_price REAL,
        date TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        action TEXT DEFAULT 'created'
    );

    CREATE TABLE IF NOT EXISTS sales_items (
        id SERIAL PRIMARY KEY,
        sale_id INTEGER,
        product_id INTEGER,
        batch_id INTEGER,
        quantity INTEGER,
        cost_price REAL,
        selling_price REAL,
        profit REAL,
        unit_id INTEGER,
        unit_quantity REAL
    );

    CREATE TABLE IF NOT EXISTS product_units (
        id SERIAL PRIMARY KEY,
        product_id INTEGER,
        unit_name TEXT,
        conversion_factor REAL,
        selling_price REAL
    );

    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );

    -- Indexes
    CREATE INDEX IF NOT EXISTS idx_purchase_batches_product_id ON purchase_batches(product_id);
    CREATE INDEX IF NOT EXISTS idx_purchase_batches_remaining ON purchase_batches(remaining_quantity);
    CREATE INDEX IF NOT EXISTS idx_sales_items_sale_id ON sales_items(sale_id);
    CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(date);
    CREATE INDEX IF NOT EXISTS idx_products_stock ON products(stock);
    CREATE INDEX IF NOT EXISTS idx_products_name ON products(name);
    CREATE INDEX IF NOT EXISTS idx_sales_payment_method ON sales(payment_method);
    CREATE INDEX IF NOT EXISTS idx_sales_user_id ON sales(user_id);  -- <-- new index
    """

    POSTGRES_MIGRATIONS = [
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS category TEXT",
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS discount REAL DEFAULT 0",
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS base_unit TEXT DEFAULT 'piece'",
        "ALTER TABLE sales ADD COLUMN IF NOT EXISTS discount REAL DEFAULT 0",
        "ALTER TABLE sales ADD COLUMN IF NOT EXISTS subtotal REAL DEFAULT 0",
        "ALTER TABLE sales ADD COLUMN IF NOT EXISTS product_id INTEGER",
        "ALTER TABLE sales ADD COLUMN IF NOT EXISTS reversed INTEGER DEFAULT 0",
        "ALTER TABLE sales ADD COLUMN IF NOT EXISTS payment_method VARCHAR(50) DEFAULT 'cash'",
        "ALTER TABLE sales ADD COLUMN IF NOT EXISTS cheque_number VARCHAR(100)",
        "ALTER TABLE sales ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id)",  # <-- new migration
        "ALTER TABLE purchases ADD COLUMN IF NOT EXISTS selling_price REAL DEFAULT 0",
        "ALTER TABLE purchases ADD COLUMN IF NOT EXISTS remaining_stock INTEGER DEFAULT 0",
        "ALTER TABLE purchase_batches ADD COLUMN IF NOT EXISTS selling_price REAL DEFAULT 0",
        "ALTER TABLE purchase_batches ADD COLUMN IF NOT EXISTS action TEXT DEFAULT 'created'",
        "ALTER TABLE deleted_products ADD COLUMN IF NOT EXISTS category TEXT",
        "ALTER TABLE deleted_products ADD COLUMN IF NOT EXISTS discount REAL DEFAULT 0",
        "ALTER TABLE deleted_products ADD COLUMN IF NOT EXISTS action TEXT DEFAULT 'deleted'",
        "ALTER TABLE deleted_products ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'product'",
        "ALTER TABLE deleted_products ADD COLUMN IF NOT EXISTS batch_id INTEGER",
        "ALTER TABLE deleted_products ADD COLUMN IF NOT EXISTS batch_quantity INTEGER",
        "ALTER TABLE deleted_products ADD COLUMN IF NOT EXISTS batch_remaining INTEGER",
        "ALTER TABLE deleted_products ADD COLUMN IF NOT EXISTS product_id INTEGER",
        "ALTER TABLE sales_items ADD COLUMN IF NOT EXISTS unit_id INTEGER",
        "ALTER TABLE sales_items ADD COLUMN IF NOT EXISTS unit_quantity REAL",
        "CREATE TABLE IF NOT EXISTS product_units (id SERIAL PRIMARY KEY, product_id INTEGER, unit_name TEXT, conversion_factor REAL, selling_price REAL)",
        "CREATE INDEX IF NOT EXISTS idx_purchase_batches_product_id ON purchase_batches(product_id)",
        "CREATE INDEX IF NOT EXISTS idx_sales_items_sale_id ON sales_items(sale_id)",
        "CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(date)",
        "CREATE INDEX IF NOT EXISTS idx_sales_payment_method ON sales(payment_method)",
        "CREATE INDEX IF NOT EXISTS idx_sales_user_id ON sales(user_id)"   # <-- new index
    ]

    def init_pool():
        """Initialize or reinitialize the connection pool"""
        global connection_pool
        if connection_pool is not None:
            try:
                connection_pool.closeall()
            except:
                pass
            connection_pool = None
        
        try:
            connection_pool = pool.SimpleConnectionPool(
                2,                     # min connections
                30,                    # max connections
                DATABASE_URL,
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=5,
                connect_timeout=10,
                sslmode='require',
                options='-c statement_timeout=30000'
            )
            # Test the pool
            test_conn = connection_pool.getconn()
            cursor = test_conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            connection_pool.putconn(test_conn)
            print("PostgreSQL connection pool created successfully")
            return True
        except Exception as e:
            print(f"Failed to create connection pool: {e}")
            connection_pool = None
            return False

    def get_connection():
        """Get a connection from the pool with retries and fallback"""
        global connection_pool
        
        # If pool doesn't exist, try to create it
        if connection_pool is None:
            if not init_pool():
                # If pool init fails, fall back to direct connection
                print("Pool init failed, using direct connection")
                return get_direct_connection()
        
        # Try to get a connection from the pool with retries
        for attempt in range(MAX_RETRIES):
            try:
                conn = connection_pool.getconn()
                # Validate connection is alive
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                cursor.fetchone()
                return conn
            except Exception as e:
                print(f"Error getting connection from pool (attempt {attempt+1}/{MAX_RETRIES}): {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    # Try to reinitialize the pool
                    init_pool()
                else:
                    # Pool exhausted or broken; fall back to direct connection
                    print("Pool exhausted, falling back to direct connection")
                    return get_direct_connection()
        
        # Should never reach here, but fallback
        return get_direct_connection()

    def get_direct_connection():
        """Create a direct connection (no pooling) with retries"""
        for attempt in range(MAX_RETRIES):
            try:
                conn = psycopg2.connect(
                    DATABASE_URL,
                    sslmode='require',
                    connect_timeout=10,
                    keepalives=1,
                    keepalives_idle=30,
                    keepalives_interval=10,
                    keepalives_count=5
                )
                # Ensure schema exists
                cursor = conn.cursor()
                cursor.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'products')")
                tables_exist = cursor.fetchone()[0]
                if not tables_exist:
                    print("Creating database schema...")
                    cursor.execute(SCHEMA)
                    for migration in POSTGRES_MIGRATIONS:
                        try:
                            cursor.execute(migration)
                        except Exception as e:
                            print(f"Migration warning: {e}")
                    conn.commit()
                    print("Database schema created successfully")
                return conn
            except Exception as e:
                print(f"Direct connection attempt {attempt+1} failed: {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                else:
                    raise

    def return_connection(conn):
        """Return connection to the pool, or close it if not pooled."""
        global connection_pool
        if conn is None:
            return
        try:
            # Check if connection is from the pool (has _pool attribute)
            if connection_pool and hasattr(conn, '_pool'):
                connection_pool.putconn(conn)
            else:
                conn.close()
        except Exception as e:
            print(f"Error returning connection: {e}")
            try:
                conn.close()
            except:
                pass

else:
    # ---------- SQLite (local development) ----------
    print("Using SQLite (local)")
    
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        brand TEXT,
        cost_price REAL,
        selling_price REAL,
        stock INTEGER,
        category TEXT,
        discount REAL DEFAULT 0,
        base_unit TEXT DEFAULT 'piece'
    );

    CREATE TABLE IF NOT EXISTS sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subtotal REAL DEFAULT 0,
        discount REAL DEFAULT 0,
        total REAL,
        profit REAL,
        date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        payment_method VARCHAR(50) DEFAULT 'cash',
        cheque_number VARCHAR(100),
        user_id INTEGER REFERENCES users(id)   -- <-- added user_id
    );

    CREATE TABLE IF NOT EXISTS deleted_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        brand TEXT,
        cost_price REAL,
        selling_price REAL,
        stock INTEGER,
        category TEXT,
        discount REAL DEFAULT 0,
        action TEXT DEFAULT 'deleted',
        source TEXT DEFAULT 'product',
        deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        batch_id INTEGER,
        batch_quantity INTEGER,
        batch_remaining INTEGER,
        product_id INTEGER
    );

    CREATE TABLE IF NOT EXISTS purchases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_name TEXT NOT NULL,
        brand TEXT,
        category TEXT,
        quantity INTEGER,
        cost_price REAL,
        discount REAL DEFAULT 0,
        total REAL,
        selling_price REAL,
        date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        remaining_stock INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS purchase_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER,
        quantity INTEGER,
        remaining_quantity INTEGER,
        cost_price REAL,
        discount REAL,
        selling_price REAL,
        date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        action TEXT DEFAULT 'created'
    );

    CREATE TABLE IF NOT EXISTS sales_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sale_id INTEGER,
        product_id INTEGER,
        batch_id INTEGER,
        quantity INTEGER,
        cost_price REAL,
        selling_price REAL,
        profit REAL,
        unit_id INTEGER,
        unit_quantity REAL
    );

    CREATE TABLE IF NOT EXISTS product_units (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER,
        unit_name TEXT,
        conversion_factor REAL,
        selling_price REAL,
        FOREIGN KEY (product_id) REFERENCES products(id)
    );

    -- Indexes for SQLite
    CREATE INDEX IF NOT EXISTS idx_purchase_batches_product_id ON purchase_batches(product_id);
    CREATE INDEX IF NOT EXISTS idx_sales_items_sale_id ON sales_items(sale_id);
    CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(date);
    CREATE INDEX IF NOT EXISTS idx_sales_payment_method ON sales(payment_method);
    CREATE INDEX IF NOT EXISTS idx_sales_user_id ON sales(user_id);   -- <-- new index
    """

    AUTH_SCHEMA = """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    
    CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
    """

    def get_connection():
        """Get SQLite connection with proper settings"""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        cursor.executescript(SCHEMA)

        # Safe migrations
        try: cursor.execute("ALTER TABLE products ADD COLUMN category TEXT")
        except: pass
        try: cursor.execute("ALTER TABLE products ADD COLUMN discount REAL DEFAULT 0")
        except: pass
        try: cursor.execute("ALTER TABLE products ADD COLUMN base_unit TEXT DEFAULT 'piece'")
        except: pass

        try: cursor.execute("ALTER TABLE sales ADD COLUMN discount REAL DEFAULT 0")
        except: pass
        try: cursor.execute("ALTER TABLE sales ADD COLUMN subtotal REAL DEFAULT 0")
        except: pass
        try: cursor.execute("ALTER TABLE sales ADD COLUMN product_id INTEGER")
        except: pass
        try: cursor.execute("ALTER TABLE sales ADD COLUMN reversed INTEGER DEFAULT 0")
        except: pass
        try: cursor.execute("ALTER TABLE sales ADD COLUMN payment_method VARCHAR(50) DEFAULT 'cash'")
        except: pass
        try: cursor.execute("ALTER TABLE sales ADD COLUMN cheque_number VARCHAR(100)")
        except: pass
        try: cursor.execute("ALTER TABLE sales ADD COLUMN user_id INTEGER REFERENCES users(id)")   # <-- new column
        except: pass

        try: cursor.execute("ALTER TABLE purchases ADD COLUMN selling_price REAL DEFAULT 0")
        except: pass
        try: cursor.execute("ALTER TABLE purchases ADD COLUMN remaining_stock INTEGER DEFAULT 0")
        except: pass

        try: cursor.execute("ALTER TABLE purchase_batches ADD COLUMN selling_price REAL DEFAULT 0")
        except: pass
        try: cursor.execute("ALTER TABLE purchase_batches ADD COLUMN action TEXT DEFAULT 'created'")
        except: pass

        try: cursor.execute("ALTER TABLE deleted_products ADD COLUMN category TEXT")
        except: pass
        try: cursor.execute("ALTER TABLE deleted_products ADD COLUMN discount REAL DEFAULT 0")
        except: pass
        try: cursor.execute("ALTER TABLE deleted_products ADD COLUMN action TEXT DEFAULT 'deleted'")
        except: pass
        try: cursor.execute("ALTER TABLE deleted_products ADD COLUMN source TEXT DEFAULT 'product'")
        except: pass
        try: cursor.execute("ALTER TABLE deleted_products ADD COLUMN batch_id INTEGER")
        except: pass
        try: cursor.execute("ALTER TABLE deleted_products ADD COLUMN batch_quantity INTEGER")
        except: pass
        try: cursor.execute("ALTER TABLE deleted_products ADD COLUMN batch_remaining INTEGER")
        except: pass
        try: cursor.execute("ALTER TABLE deleted_products ADD COLUMN product_id INTEGER")
        except: pass

        try: cursor.execute("ALTER TABLE sales_items ADD COLUMN unit_id INTEGER")
        except: pass
        try: cursor.execute("ALTER TABLE sales_items ADD COLUMN unit_quantity REAL")
        except: pass

        try:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS product_units (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER,
                    unit_name TEXT,
                    conversion_factor REAL,
                    selling_price REAL,
                    FOREIGN KEY (product_id) REFERENCES products(id)
                )
            """)
        except: pass

        # Indexes
        try: cursor.execute("CREATE INDEX IF NOT EXISTS idx_purchase_batches_product_id ON purchase_batches(product_id)")
        except: pass
        try: cursor.execute("CREATE INDEX IF NOT EXISTS idx_sales_items_sale_id ON sales_items(sale_id)")
        except: pass
        try: cursor.execute("CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(date)")
        except: pass
        try: cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_stock ON products(stock)")
        except: pass
        try: cursor.execute("CREATE INDEX IF NOT EXISTS idx_sales_payment_method ON sales(payment_method)")
        except: pass
        try: cursor.execute("CREATE INDEX IF NOT EXISTS idx_sales_user_id ON sales(user_id)")   # <-- new index
        except: pass

        conn.commit()

        # Auth DB
        auth_conn = sqlite3.connect(AUTH_DB_PATH)
        auth_cursor = auth_conn.cursor()
        auth_cursor.executescript(AUTH_SCHEMA)
        auth_conn.commit()
        auth_conn.close()

        return conn

    def return_connection(conn):
        if conn:
            try:
                conn.close()
            except:
                pass

# ---------- Helper function to get parameter style ----------
def get_param_style(cursor):
    if hasattr(cursor, 'connection'):
        if hasattr(cursor.connection, 'psycopg2_version'):
            return "%s"  # PostgreSQL
    return "?"  # SQLite

# ---------- Context manager for automatic connection handling ----------
@contextmanager
def get_db_connection():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        return_connection(conn)

# ---------- Health check function ----------
def check_database_health():
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        return_connection(conn)
        return True
    except Exception as e:
        print(f"Database health check failed: {e}")
        return False

# ---------- Close all connections (for shutdown) ----------
def close_all_connections():
    global connection_pool
    if connection_pool:
        try:
            connection_pool.closeall()
            print("All database connections closed")
        except Exception as e:
            print(f"Error closing connection pool: {e}")
        finally:
            connection_pool = None