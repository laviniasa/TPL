from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()


# =========================================================
# USUÁRIO
# =========================================================

class Usuario(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    nome = db.Column(
        db.String(100),
        nullable=False
    )

    email = db.Column(
        db.String(120),
        unique=True,
        nullable=False
    )

    senha = db.Column(
        db.String(200),
        nullable=False
    )

    tipo = db.Column(
        db.String(20),
        default="IRMAO"
    )

    ativo = db.Column(
        db.Boolean,
        default=True
    )


# =========================================================
# PESSOA
# Cadastro dos irmãos que podem participar das reservas
# =========================================================

class Pessoa(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    nome = db.Column(
        db.String(100),
        nullable=False,
        unique=True
    )

    ativo = db.Column(
        db.Boolean,
        default=True
    )


# =========================================================
# CARRINHO
# =========================================================

class Carrinho(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    nome = db.Column(
        db.String(50),
        nullable=False
    )

    ativo = db.Column(
        db.Boolean,
        default=True
    )


# =========================================================
# LOCAL
# =========================================================

class Local(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    nome = db.Column(
        db.String(100),
        nullable=False
    )

    ativo = db.Column(
        db.Boolean,
        default=True
    )


# =========================================================
# PROGRAMAÇÃO
# =========================================================

class Programacao(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    data = db.Column(
        db.Date,
        nullable=False
    )

    hora_inicio = db.Column(
        db.Time,
        nullable=False
    )

    hora_fim = db.Column(
        db.Time,
        nullable=False
    )

    carrinho_id = db.Column(
        db.Integer,
        db.ForeignKey("carrinho.id"),
        nullable=False
    )

    local_id = db.Column(
        db.Integer,
        db.ForeignKey("local.id"),
        nullable=False
    )

    quantidade_maxima = db.Column(
        db.Integer,
        default=3
    )

    carrinho = db.relationship(
        "Carrinho"
    )

    local = db.relationship(
        "Local"
    )


# =========================================================
# INSCRIÇÃO — SISTEMA ANTIGO
# =========================================================

class Inscricao(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    programacao_id = db.Column(
        db.Integer,
        db.ForeignKey("programacao.id"),
        nullable=False
    )

    nome_principal = db.Column(
        db.String(100),
        nullable=False
    )

    criado_em = db.Column(
        db.DateTime,
        default=db.func.now()
    )

    programacao = db.relationship(
        "Programacao"
    )


# =========================================================
# PARTICIPANTE — SISTEMA ANTIGO
# =========================================================

class Participante(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    inscricao_id = db.Column(
        db.Integer,
        db.ForeignKey("inscricao.id"),
        nullable=False
    )

    nome = db.Column(
        db.String(100),
        nullable=False
    )

    inscricao = db.relationship(
        "Inscricao",
        backref="participantes"
    )


# =========================================================
# RESERVA
# =========================================================

class Reserva(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    nome_principal = db.Column(
        db.String(100),
        nullable=False
    )

    tipo = db.Column(
        db.String(20),
        nullable=False,
        default="UNICA"
    )

    data_inicio = db.Column(
        db.Date,
        nullable=False
    )

    data_fim = db.Column(
        db.Date,
        nullable=True
    )

    dia_semana = db.Column(
        db.Integer,
        nullable=True
    )

    hora_inicio = db.Column(
        db.Time,
        nullable=False
    )

    hora_fim = db.Column(
        db.Time,
        nullable=False
    )

    carrinho_id = db.Column(
        db.Integer,
        db.ForeignKey("carrinho.id"),
        nullable=False
    )

    local_id = db.Column(
        db.Integer,
        db.ForeignKey("local.id"),
        nullable=False
    )

    ativo = db.Column(
        db.Boolean,
        default=True
    )

    carrinho = db.relationship(
        "Carrinho"
    )

    local = db.relationship(
        "Local"
    )


# =========================================================
# PARTICIPANTES DA RESERVA
# =========================================================

class ReservaParticipante(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    reserva_id = db.Column(
        db.Integer,
        db.ForeignKey("reserva.id"),
        nullable=False
    )

    nome = db.Column(
        db.String(100),
        nullable=False
    )

    reserva = db.relationship(
        "Reserva",
        backref="participantes"
    )