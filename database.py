
def get_order_items(order_id):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            product_name,
            quantity
        FROM order_items
        WHERE order_id = ?
        ORDER BY id ASC
        """,
        (order_id,),
    )

    items = cursor.fetchall()
    connection.close()

    return items

