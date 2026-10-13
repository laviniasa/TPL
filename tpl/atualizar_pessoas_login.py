import sqlite3
import os


# Caminho do banco usado pelo Flask
banco = os.path.join("instance", "tpl.db")

print("Banco:", os.path.abspath(banco))


conn = sqlite3.connect(banco)

cursor = conn.cursor()


# Verifica as colunas atuais da tabela pessoa
cursor.execute("PRAGMA table_info(pessoa)")

colunas = cursor.fetchall()

nomes_colunas = [coluna[1] for coluna in colunas]


if "usuario_id" not in nomes_colunas:

    print("Adicionando coluna usuario_id...")

    cursor.execute("""
        ALTER TABLE pessoa
        ADD COLUMN usuario_id INTEGER
    """)

    conn.commit()

    print("Coluna usuario_id adicionada com sucesso.")

else:

    print("Coluna usuario_id já existe.")


conn.close()

print("Atualização do banco concluída.")