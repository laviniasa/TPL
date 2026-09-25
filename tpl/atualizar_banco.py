from app import create_app
from app.models import db


app = create_app()


with app.app_context():

    with db.engine.connect() as conn:

        colunas = conn.exec_driver_sql(
            "PRAGMA table_info(pessoa)"
        ).fetchall()

        nomes_colunas = [
            coluna[1]
            for coluna in colunas
        ]

        if "usuario_id" not in nomes_colunas:

            print("Adicionando coluna usuario_id...")

            conn.exec_driver_sql(
                """
                ALTER TABLE pessoa
                ADD COLUMN usuario_id INTEGER
                """
            )

            conn.commit()

            print("Coluna usuario_id adicionada.")

        else:

            print("Coluna usuario_id já existe.")


print("Atualização do banco concluída.")