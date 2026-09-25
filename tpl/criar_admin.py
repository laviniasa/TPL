from getpass import getpass

from werkzeug.security import generate_password_hash

from app import create_app
from app.models import db, Usuario


app = create_app()


with app.app_context():

    print()
    print("==============================")
    print(" CRIAR USUÁRIO ADMINISTRADOR")
    print("==============================")
    print()

    nome = input("Nome: ").strip()
    email = input("E-mail: ").strip().lower()

    senha = getpass("Senha: ")
    confirmar = getpass("Confirme a senha: ")

    if not nome:
        print("Erro: informe o nome.")
        raise SystemExit

    if not email:
        print("Erro: informe o e-mail.")
        raise SystemExit

    if not senha:
        print("Erro: informe uma senha.")
        raise SystemExit

    if senha != confirmar:
        print("Erro: as senhas não são iguais.")
        raise SystemExit

    usuario_existente = Usuario.query.filter_by(
        email=email
    ).first()

    if usuario_existente:
        print()
        print("Erro: já existe um usuário com este e-mail.")
        raise SystemExit

    usuario = Usuario(
        nome=nome,
        email=email,
        senha=generate_password_hash(senha),
        tipo="ADMIN",
        ativo=True
    )

    db.session.add(usuario)
    db.session.commit()

    print()
    print("==============================")
    print(" ADMINISTRADOR CRIADO!")
    print("==============================")
    print()
    print(f"Nome: {nome}")
    print(f"E-mail: {email}")
    print()