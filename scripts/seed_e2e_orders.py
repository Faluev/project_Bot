#!/usr/bin/env python3
"""Seed realistic fake orders for manual Telegram E2E testing."""

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Allow the script to import project modules when launched as
# "python scripts/seed_e2e_orders.py" from the repository root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database


ORDERS = [
    {
        "number": "E2E-REAL-001",
        "client": "Мадина Саидова",
        "phone": "+992 90 100 1001",
        "address": "Душанбе, ул. Рудаки, 105, кв. 24",
        "comment": "Позвонить за 5 минут до приезда.",
        "payment": "cash",
        "amount": 186.50,
        "items": [("Молоко 3.2%, 1 л", 2), ("Хлеб Бородинский", 1), ("Яблоки Голден", 1)],
    },
    {
        "number": "E2E-REAL-002",
        "client": "Фарид Нуров",
        "phone": "+992 93 100 1002",
        "address": "Душанбе, ул. Айни, 42, кв. 17",
        "comment": "Домофон 17, оставить пакет у двери нельзя.",
        "payment": "card",
        "amount": 324.00,
        "items": [("Вода 1.5 л", 6), ("Куриное филе", 1), ("Рис длиннозёрный", 1)],
    },
    {
        "number": "E2E-REAL-003",
        "client": "Зарина Каримова",
        "phone": "+992 98 100 1003",
        "address": "Душанбе, пр. Исмоили Сомони, 18, кв. 9",
        "comment": "Без замен по товарам.",
        "payment": "cash",
        "amount": 279.00,
        "items": [("Помидоры", 1), ("Огурцы", 1), ("Сыр Гауда", 1), ("Йогурт натуральный", 4)],
    },
    {
        "number": "E2E-REAL-004",
        "client": "Далер Хакимов",
        "phone": "+992 90 100 1004",
        "address": "Душанбе, ул. Нусратулло Махсум, 12, кв. 31",
        "comment": "Курьеру позвонить, если дверь закрыта.",
        "payment": "card",
        "amount": 412.75,
        "items": [("Говядина", 1), ("Картофель", 2), ("Лук репчатый", 1), ("Масло подсолнечное", 1)],
    },
    {
        "number": "E2E-REAL-005",
        "client": "Шахноза Олимова",
        "phone": "+992 93 100 1005",
        "address": "Душанбе, ул. Мирзо Турсунзаде, 76, кв. 12",
        "comment": "Пожалуйста, выбрать бананы без повреждений.",
        "payment": "cash",
        "amount": 198.00,
        "items": [("Бананы", 1), ("Апельсины", 1), ("Хлеб белый", 2)],
    },
    {
        "number": "E2E-REAL-006",
        "client": "Рустам Назаров",
        "phone": "+992 98 100 1006",
        "address": "Душанбе, ул. Бухоро, 27, кв. 5",
        "comment": "Если товара нет, сначала сообщить администратору.",
        "payment": "card",
        "amount": 365.20,
        "items": [("Кофе молотый", 1), ("Сахар", 1), ("Печенье овсяное", 2), ("Молоко 3.2%, 1 л", 2)],
    },
    {
        "number": "E2E-REAL-007",
        "client": "Ситора Абдуллоева",
        "phone": "+992 90 100 1007",
        "address": "Душанбе, ул. Карабаева, 33, кв. 44",
        "comment": "Доставка после 15:00.",
        "payment": "cash",
        "amount": 241.00,
        "items": [("Яйца С1", 10), ("Сметана", 1), ("Сыр плавленый", 2)],
    },
    {
        "number": "E2E-REAL-008",
        "client": "Бехруз Сафаров",
        "phone": "+992 93 100 1008",
        "address": "Душанбе, ул. Шотемур, 61, кв. 8",
        "comment": "Подъезд 2, этаж 3.",
        "payment": "card",
        "amount": 489.90,
        "items": [("Минеральная вода 1.5 л", 6), ("Сок апельсиновый", 2), ("Куриные крылышки", 1), ("Макароны", 2)],
    },
    {
        "number": "E2E-REAL-009",
        "client": "Нигина Мирзоева",
        "phone": "+992 98 100 1009",
        "address": "Душанбе, ул. Фирдавси, 91, кв. 16",
        "comment": "Не заменять детское питание.",
        "payment": "cash",
        "amount": 536.00,
        "items": [("Детское пюре", 4), ("Детское печенье", 2), ("Бананы", 1), ("Вода детская", 4)],
    },
    {
        "number": "E2E-REAL-010",
        "client": "Комрон Юсуфов",
        "phone": "+992 90 100 1010",
        "address": "Душанбе, ул. Садриддина Айни, 15, кв. 22",
        "comment": "Позвонить клиенту перед выездом.",
        "payment": "card",
        "amount": 617.40,
        "items": [("Филе индейки", 1), ("Гречка", 2), ("Овсяные хлопья", 1), ("Оливковое масло", 1)],
    },
]


def seed(reset=False):
    database.init_database()
    connection = sqlite3.connect(database.DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        connection.execute("BEGIN IMMEDIATE")

        if reset:
            rows = connection.execute(
                "SELECT id FROM orders WHERE order_number LIKE 'TEST-E2E-%' OR order_number LIKE 'E2E-REAL-%'"
            ).fetchall()
            ids = [row[0] for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(f"DELETE FROM action_log WHERE order_id IN ({placeholders})", ids)
                connection.execute(f"DELETE FROM order_items WHERE order_id IN ({placeholders})", ids)
                connection.execute(f"DELETE FROM orders WHERE id IN ({placeholders})", ids)

        # Фикстуры идут от самого старого к самому новому заказу:
        # E2E-REAL-001 оформлен раньше E2E-REAL-010.
        base_time = (datetime.now() - timedelta(minutes=len(ORDERS) - 1)).replace(microsecond=0)
        created = 0
        for index, order in enumerate(ORDERS):
            existing = connection.execute(
                "SELECT id FROM orders WHERE order_number = ?",
                (order["number"],),
            ).fetchone()
            if existing:
                continue

            created_at = (base_time + timedelta(minutes=index)).strftime("%Y-%m-%d %H:%M:%S")
            cursor = connection.execute(
                """
                INSERT INTO orders (
                    order_number, client_name, client_phone, delivery_address,
                    client_comment, payment_method, payment_amount, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)
                """,
                (
                    order["number"], order["client"], order["phone"],
                    order["address"], order["comment"], order["payment"],
                    order["amount"], created_at, created_at,
                ),
            )
            order_id = cursor.lastrowid
            for product_name, quantity in order["items"]:
                connection.execute(
                    "INSERT INTO order_items (order_id, product_name, quantity) VALUES (?, ?, ?)",
                    (order_id, product_name, quantity),
                )
            created += 1

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    print(f"E2E REAL ORDERS CREATED: {created}")
    print("Order queue: E2E-REAL-001 ... E2E-REAL-010 (oldest -> newest)")
    print("Each order contains client, phone, address, created time, comment, payment, amount and products.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Remove previous TEST-E2E-* and E2E-REAL-* fixtures before seeding.",
    )
    args = parser.parse_args()
    seed(reset=args.reset)
