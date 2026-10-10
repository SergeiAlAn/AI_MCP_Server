"""Работа с локальной базой SQLite3 (products.db)."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "products.db"

SEED_PRODUCTS: list[tuple[str, str, float]] = [
    # Электроника
    ("Смартфон Samsung Galaxy A55", "Электроника", 34990.0),
    ("Смартфон Xiaomi Redmi Note 13", "Электроника", 21990.0),
    ("Ноутбук Lenovo IdeaPad 5", "Электроника", 64990.0),
    ("Наушники Sony WH-1000XM5", "Электроника", 32990.0),
    ("Беспроводные наушники JBL Tune 520", "Электроника", 4990.0),
    ("Планшет Apple iPad 10", "Электроника", 42990.0),
    ("Умные часы Huawei Watch Fit 3", "Электроника", 11990.0),
    ("Электронная книга PocketBook 629", "Электроника", 15490.0),
    ("Портативная колонка Яндекс Станция Мини", "Электроника", 7990.0),
    ("Пауэрбанк Anker 20000 мАч", "Электроника", 3990.0),
    # Одежда
    ("Футболка хлопковая белая", "Одежда", 1290.0),
    ("Джинсы Levi's 501", "Одежда", 7990.0),
    ("Куртка зимняя пуховая", "Одежда", 12990.0),
    ("Худи оверсайз серое", "Одежда", 3490.0),
    ("Кроссовки Nike Air Max", "Одежда", 13990.0),
    ("Шапка вязаная", "Одежда", 990.0),
    ("Рубашка льняная голубая", "Одежда", 2790.0),
    ("Платье летнее в цветочек", "Одежда", 3990.0),
    ("Носки спортивные (5 пар)", "Одежда", 690.0),
    ("Пальто шерстяное", "Одежда", 18990.0),
    # Продукты
    ("Хлеб бородинский", "Продукты", 65.0),
    ("Молоко 3,2% 1 л", "Продукты", 95.0),
    ("Сыр Российский 200 г", "Продукты", 249.0),
    ("Яйца куриные С1 (10 шт)", "Продукты", 119.0),
    ("Гречка ядрица 900 г", "Продукты", 109.0),
    ("Макароны Barilla Spaghetti", "Продукты", 189.0),
    ("Масло сливочное 82,5%", "Продукты", 229.0),
    ("Шоколад Alpen Gold молочный", "Продукты", 99.0),
    ("Рис басмати 1 кг", "Продукты", 259.0),
    ("Мёд цветочный 500 г", "Продукты", 449.0),
    # Напитки
    ("Чай зелёный Greenfield", "Напитки", 189.0),
    ("Чай чёрный Ahmad Tea Earl Grey", "Напитки", 299.0),
    ("Чай травяной ромашковый", "Напитки", 159.0),
    ("Кофе молотый Lavazza Oro", "Напитки", 899.0),
    ("Кофе в зёрнах Jardin Espresso", "Напитки", 749.0),
    ("Сок апельсиновый Rich 1 л", "Напитки", 179.0),
    ("Минеральная вода Боржоми 0,5 л", "Напитки", 119.0),
    ("Какао Nesquik 500 г", "Напитки", 349.0),
    ("Лимонад Буратино 1,5 л", "Напитки", 89.0),
    ("Морс клюквенный 1 л", "Напитки", 139.0),
    # Книги
    ("Книга «Мастер и Маргарита»", "Книги", 590.0),
    ("Книга «Преступление и наказание»", "Книги", 450.0),
    ("Книга «Чистый код» Роберт Мартин", "Книги", 1890.0),
    ("Книга «Грокаем алгоритмы»", "Книги", 1490.0),
    ("Книга «Гарри Поттер и философский камень»", "Книги", 790.0),
    ("Книга «1984» Джордж Оруэлл", "Книги", 520.0),
    ("Книга «Python. К вершинам мастерства»", "Книги", 3290.0),
    ("Книга «Война и мир» (2 тома)", "Книги", 1190.0),
    ("Книга «Атомные привычки»", "Книги", 890.0),
    ("Книга «Маленький принц»", "Книги", 390.0),
    # Спорт
    ("Гантели разборные 2×10 кг", "Спорт", 4990.0),
    ("Коврик для йоги 6 мм", "Спорт", 1490.0),
    ("Скакалка скоростная", "Спорт", 590.0),
    ("Мяч футбольный Adidas", "Спорт", 2990.0),
    ("Велосипед горный Stels Navigator", "Спорт", 24990.0),
    ("Ракетка для настольного тенниса", "Спорт", 1290.0),
    ("Фитнес-резинки (набор 5 шт)", "Спорт", 790.0),
    ("Бутылка для воды спортивная", "Спорт", 690.0),
    ("Турник настенный", "Спорт", 3490.0),
    ("Самокат городской Xiaomi", "Спорт", 8990.0),
    # Дом
    ("Чайник электрический Bosch", "Дом", 3490.0),
    ("Пылесос робот Xiaomi", "Дом", 19990.0),
    ("Сковорода Tefal 28 см", "Дом", 2990.0),
    ("Набор кастрюль (3 шт)", "Дом", 5490.0),
    ("Постельное бельё евро", "Дом", 3990.0),
    ("Подушка ортопедическая", "Дом", 2490.0),
    ("Настольная лампа LED", "Дом", 1990.0),
    ("Микроволновая печь LG", "Дом", 8990.0),
    ("Набор полотенец (4 шт)", "Дом", 1790.0),
    ("Кофемашина De'Longhi Magnifica", "Дом", 39990.0),
    # Красота
    ("Шампунь Head & Shoulders", "Красота", 499.0),
    ("Крем для рук Nivea", "Красота", 249.0),
    ("Зубная паста Colgate", "Красота", 189.0),
    ("Электрическая зубная щётка Oral-B", "Красота", 4490.0),
    ("Фен Philips 2100 Вт", "Красота", 2990.0),
    ("Туалетная вода Hugo Boss", "Красота", 6990.0),
    ("Гель для душа Palmolive", "Красота", 299.0),
    ("Солнцезащитный крем SPF 50", "Красота", 899.0),
    ("Бритва Gillette Fusion", "Красота", 1290.0),
    ("Маска для лица тканевая (5 шт)", "Красота", 590.0),
    # Игрушки
    ("Конструктор LEGO City", "Игрушки", 4990.0),
    ("Кукла Barbie", "Игрушки", 2490.0),
    ("Пазл 1000 элементов", "Игрушки", 890.0),
    ("Настольная игра «Монополия»", "Игрушки", 2990.0),
    ("Машинка на радиоуправлении", "Игрушки", 3490.0),
    ("Мягкая игрушка Медведь 50 см", "Игрушки", 1490.0),
    ("Кубик Рубика 3×3", "Игрушки", 590.0),
    ("Настольная игра «Каркассон»", "Игрушки", 2290.0),
    ("Набор для рисования (48 предметов)", "Игрушки", 1290.0),
    ("Железная дорога детская", "Игрушки", 5990.0),
    # Канцелярия
    ("Ручка шариковая синяя (10 шт)", "Канцелярия", 199.0),
    ("Тетрадь в клетку 96 листов", "Канцелярия", 129.0),
    ("Блокнот Moleskine A5", "Канцелярия", 1990.0),
    ("Карандаши цветные (24 шт)", "Канцелярия", 349.0),
    ("Маркеры текстовые (4 шт)", "Канцелярия", 259.0),
    ("Степлер металлический", "Канцелярия", 449.0),
    ("Бумага А4 500 листов", "Канцелярия", 499.0),
    ("Папка-регистратор", "Канцелярия", 229.0),
    ("Ежедневник датированный 2026", "Канцелярия", 890.0),
    ("Калькулятор настольный Casio", "Канцелярия", 990.0),
]


def _py_lower(value):
    # SQLite LOWER() понимает только ASCII, поэтому для кириллицы используем Python.
    return value.lower() if isinstance(value, str) else value


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.create_function("py_lower", 1, _py_lower, deterministic=True)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Создаёт таблицу и при первом запуске заполняет её 100 тестовыми товарами."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                category TEXT,
                price REAL
            )
            """
        )
        (count,) = conn.execute("SELECT COUNT(*) FROM products").fetchone()
        if count == 0:
            conn.executemany(
                "INSERT INTO products (name, category, price) VALUES (?, ?, ?)",
                SEED_PRODUCTS,
            )


def _rows_to_dicts(rows) -> list[dict]:
    return [dict(row) for row in rows]


def get_all_products() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, category, price FROM products ORDER BY category, id"
        ).fetchall()
    return _rows_to_dicts(rows)


def search_products_by_name(name: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, category, price FROM products "
            "WHERE py_lower(name) LIKE ? ORDER BY id",
            (f"%{name.lower()}%",),
        ).fetchall()
    return _rows_to_dicts(rows)


def search_products_by_category(category: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, category, price FROM products "
            "WHERE py_lower(category) LIKE ? ORDER BY id",
            (f"%{category.lower()}%",),
        ).fetchall()
    return _rows_to_dicts(rows)


def search_products_by_categories(categories: list[str]) -> list[dict]:
    if not categories:
        return get_all_products()
    conditions = " OR ".join("py_lower(category) LIKE ?" for _ in categories)
    params = [f"%{c.lower()}%" for c in categories]
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT id, name, category, price FROM products WHERE {conditions} ORDER BY id",
            params,
        ).fetchall()
    return _rows_to_dicts(rows)


def insert_product(name: str, category: str, price: float) -> dict:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO products (name, category, price) VALUES (?, ?, ?)",
            (name, category, price),
        )
        product_id = cursor.lastrowid
    return {"id": product_id, "name": name, "category": category, "price": price}
