from database import get_connection, init_database


TEST_ORDERS = [
    {
        "order_number": "TEST-0002",
        "client_name": "Тестовый клиент 2",
        "client_phone": "+992900000002",
        "delivery_address": "Душанбе, Рудаки 20",
        "client_comment": "Тест нового заказа для сборщика.",
        "payment_method": "cash",
        "payment_amount": 120.00,
        "status": "new",
        "items": [("Молоко", 1), ("Хлеб", 2)],
    },
    {
        "order_number": "TEST-0003",
        "client_name": "Тестовый клиент 3",
        "client_phone": "+992900000003",
        "delivery_address": "Душанбе, Айни 30",
        "client_comment": "Тест заказа, ожидающего курьера.",
        "payment_method": "cash",
        "payment_amount": 240.00,
        "status": "awaiting_courier",
        "items": [("Вода", 3), ("Яблоки", 2)],
    },
    {
        "order_number": "TEST-0004",
        "client_name": "Тестовый клиент 4",
        "client_phone": "+992900000004",
        "delivery_address": "Душанбе, Сино 40",
        "client_comment": "Тест уже назначенного курьеру заказа.",
        "payment_method": "cash",
        "payment_amount": 360.00,
        "status": "in_delivery",
        "items": [("Сок", 2), ("Печенье", 3)],
    },
]


def get_approved_employee(cursor, role):
    cursor.execute(
        """
        SELECT id
        FROM employees
        WHERE role = ?
          AND application_status = 'approved'
          AND is_active = 1
        ORDER BY id
        LIMIT 1
        """,
        (role,),
    )
    return cursor.fetchone()


def create_test_orders():
    init_database()

    connection = get_connection()
    cursor = connection.cursor()

    try:
        courier = get_approved_employee(cursor, "courier")
        if courier is None:
            print("❌ Нет одобренного активного курьера. Сначала одобрите курьера в админке.")
            return

        for test_order in TEST_ORDERS:
            cursor.execute(
                "SELECT id FROM orders WHERE order_number = ?",
                (test_order["order_number"],),
            )
            existing = cursor.fetchone()

            if existing:
                print(f"ℹ️ {test_order['order_number']} уже существует — пропускаю.")
                continue

            courier_id = courier["id"] if test_order["status"] == "in_delivery" else None

            cursor.execute(
                """
                INSERT INTO orders (
                    order_number,
                    client_name,
                    client_phone,
                    delivery_address,
                    client_comment,
                    payment_method,
                    payment_amount,
                    status,
                    courier_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    test_order["order_number"],
                    test_order["client_name"],
                    test_order["client_phone"],
                    test_order["delivery_address"],
                    test_order["client_comment"],
                    test_order["payment_method"],
                    test_order["payment_amount"],
                    test_order["status"],
                    courier_id,
                ),
            )

            order_id = cursor.lastrowid

            for product_name, quantity in test_order["items"]:
                cursor.execute(
                    """
                    INSERT INTO order_items (
                        order_id,
                        product_name,
                        quantity
                    )
                    VALUES (?, ?, ?)
                    """,
                    (order_id, product_name, quantity),
                )

            print(
                f"✅ Создан {test_order['order_number']}: "
                f"status={test_order['status']}, courier_id={courier_id}"
            )

        connection.commit()
        print("Готово: TEST-0002, TEST-0003 и TEST-0004 добавлены (существующие пропущены).")

    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    create_test_orders()
