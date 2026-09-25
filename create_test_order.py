from database import get_connection, init_database


def create_test_order():
    init_database()

    connection = get_connection()
    cursor = connection.cursor()

    try:
        # Номер нашего тестового заказа
        order_number = "TEST-0001"

        # Проверяем, существует ли уже такой заказ
        cursor.execute(
            """
            SELECT id
            FROM orders
            WHERE order_number = ?
            """,
            (order_number,),
        )

        existing_order = cursor.fetchone()

        if existing_order:
            print(
                f"Заказ {order_number} уже существует. "
                f"Новый заказ не создаём."
            )
            return

        # 1. Создаём заказ
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
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_number,
                "Тестовый клиент",
                "+992900000000",
                "Душанбе, проспект Рудаки, дом 100, "
                "подъезд 2, этаж 5, квартира 25",
                "Позвонить за 10 минут до доставки.",
                "cash",
                85.50,
                "new",
            ),
        )

        order_id = cursor.lastrowid

        # 2. Добавляем товары
        items = [
            ("Молоко 2.5%", 2),
            ("Хлеб", 1),
            ("Яблоки", 2),
        ]

        for product_name, quantity in items:
            cursor.execute(
                """
                INSERT INTO order_items (
                    order_id,
                    product_name,
                    quantity
                )
                VALUES (?, ?, ?)
                """,
                (
                    order_id,
                    product_name,
                    quantity,
                ),
            )

        connection.commit()

        print("✅ Заказ успешно создан!")
        print(f"Номер заказа: {order_number}")
        print(f"ID заказа: {order_id}")
        print("Статус: new")
        print("Товары:")

        for product_name, quantity in items:
            print(f"  - {product_name}: {quantity} шт.")

    except Exception as error:
        connection.rollback()
        print("❌ Ошибка при создании заказа:")
        print(error)

    finally:
        connection.close()


if __name__ == "__main__":
    create_test_order()