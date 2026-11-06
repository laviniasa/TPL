from flask import Flask
import os
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
    database_url = os.environ.get(
        "DATABASE_URL",
        "sqlite:///tpl.db"
    )
    
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://",
            "postgresql+psycopg://",
            1
        )
    
    if database_url.startswith("postgres://"):
        database_url = database_url.replace(
            "postgres://",
            "postgresql+psycopg://",
            1
        )
    
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
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

    from app.territorios_routes import territorios, ficha
    app.register_blueprint(territorios)
    app.register_blueprint(ficha)

    # =========================
    # CRIAÇÃO DOS DADOS INICIAIS
    # =========================

    with app.app_context():
        db.create_all()

        # Ajuste automático: acrescenta o campo "local_id" na tabela
        # de campanhas, caso ela tenha sido criada antes desse campo.
        from sqlalchemy import inspect, text

        colunas = [c["name"] for c in inspect(db.engine).get_columns("campanha")]

        if "local_id" not in colunas:
            try:
                with db.engine.begin() as conexao:
                    conexao.execute(text(
                        "ALTER TABLE campanha ADD COLUMN local_id INTEGER REFERENCES local(id)"
                    ))
            except Exception:
                # Outro processo do servidor já fez o ajuste ao mesmo tempo
                pass

        # Territórios: colunas que entraram depois da criação
        # das tabelas (o create_all não altera tabelas existentes).
        colunas_novas = [
            ("territorio", "ruas", "TEXT"),
            ("designacao_territorio", "codigo", "VARCHAR(40)"),
            ("designacao_territorio", "mapa_enviado_em", "TIMESTAMP"),
            ("dirigente_territorio", "whatsapp", "VARCHAR(20)"),
        ]

        for tabela, coluna, tipo in colunas_novas:
            existentes = [c["name"] for c in inspect(db.engine).get_columns(tabela)]

            if coluna not in existentes:
                try:
                    with db.engine.begin() as conexao:
                        conexao.execute(text(
                            f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo}"
                        ))
                except Exception:
                    # Outro processo do servidor já fez o ajuste
                    pass

        # Correção (uma vez só): um erro no cadastro salvava todo
        # território como inativo. Reativa os que foram salvos assim.
        from app.models import ConfigTerritorio, Territorio

        if not db.session.get(ConfigTerritorio, "correcao_ativo_v1"):
            try:
                Territorio.query.update({"ativo": True})
                db.session.add(ConfigTerritorio(chave="correcao_ativo_v1", valor="1"))
                db.session.commit()
            except Exception:
                db.session.rollback()

        # Campanhas antigas sem local: usa o local dos horários delas
        from app.models import Campanha

        for campanha in Campanha.query.filter(Campanha.local_id.is_(None)).all():
            if campanha.horarios:
                campanha.local_id = campanha.horarios[0].local_id

        db.session.commit()

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
