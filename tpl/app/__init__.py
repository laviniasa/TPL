from flask import Flask

from flask_login import LoginManager

from app.models import (
    db,
    Usuario,
    Carrinho,
    Local,
    Pessoa
)


def create_app():
    app = Flask(__name__)

    # =========================
    # CONFIGURAÇÕES
    # =========================

    app.config["SECRET_KEY"] = "uma-chave-secreta-tpl"
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///tpl.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # =========================
    # LOGIN
    # =========================

    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = "routes.login"

    @login_manager.user_loader
    def carregar_usuario(usuario_id):
        return Usuario.query.get(int(usuario_id))

    # =========================
    # BANCO DE DADOS
    # =========================

    db.init_app(app)

    # =========================
    # ROTAS
    # =========================

    from app.routes import routes
    app.register_blueprint(routes)

    # =========================
    # CRIAÇÃO DOS DADOS INICIAIS
    # =========================

    with app.app_context():
        db.create_all()

        # =========================
        # CRIAR CARRINHOS
        # =========================

        if Carrinho.query.count() == 0:
            db.session.add(
                Carrinho(nome="Carrinho 1")
            )

            db.session.add(
                Carrinho(nome="Carrinho 2")
            )

            db.session.add(
                Carrinho(nome="Carrinho 3")
            )

        # =========================
        # CRIAR LOCAIS
        # =========================

        if Local.query.count() == 0:
            locais = [
                "Praça Central",
                "Rodoviária",
                "Centro da Cidade",
                "Parque Municipal"
            ]

            for nome in locais:
                db.session.add(
                    Local(nome=nome)
                )

        # =========================
        # CRIAR PESSOAS
        # =========================

        pessoas = [
            "Adriana",
            "Ana Laura",
            "Andréia",
            "Cassiana",
            "Deise",
            "Diego",
            "Ederson",
            "Evaldo",
            "Evelise",
            "Flaviane",
            "Heloisa",
            "Isabel Lamin",
            "João",
            "José Carlos",
            "José Rocha",
            "Juliana",
            "Karina",
            "Lavínia",
            "Lívia",
            "Luana",
            "Lucélia",
            "Luiza Ferrete",
            "Luiza Pereira",
            "Marinete",
            "Mauro",
            "Nathalia",
            "Raphael",
            "Rebeca",
            "Ricardo",
            "Romeu",
            "Serginho Merendi",
            "Sérgio Ribeiro",
            "Sérgio Toledo",
            "Tiago",
            "Veraldo",
            "Vinicius Andrade",
            "Vinicius Gimenes",
            "Yuri"
        ]

        for nome in pessoas:
            pessoa_existente = Pessoa.query.filter_by(
                nome=nome
            ).first()

            if not pessoa_existente:
                db.session.add(
                    Pessoa(nome=nome)
                )

        # =========================
        # SALVAR
        # =========================

        db.session.commit()

    return app