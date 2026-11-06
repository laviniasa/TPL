from datetime import datetime

from flask import url_for
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

    # =====================================================
    # FLASK-LOGIN
    # =====================================================

    @property
    def is_active(self):
        return self.ativo

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def get_id(self):
        return str(self.id)


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

    # =====================================================
    # FOTO DE PERFIL
    #
    # A imagem fica guardada no banco (tabela foto_pessoa),
    # porque o disco do Render apaga os arquivos enviados
    # sempre que o servidor reinicia.
    #
    # Este campo guarda "db:<versão>". A versão muda a cada
    # nova foto, para o celular não mostrar a antiga do cache.
    # Valores antigos ("uploads/fotos/...") são ignorados.
    # Se for NULL, o perfil mostra a inicial do nome.
    # =====================================================

    foto = db.Column(
        db.String(200),
        nullable=True
    )

    @property
    def foto_url(self):
        if self.foto and self.foto.startswith("db:"):
            return url_for(
                "routes.foto_pessoa",
                pessoa_id=self.id,
                v=self.foto[3:]
            )
        return None

    # =====================================================
    # CONTA DE LOGIN
    #
    # Nem toda pessoa precisa ter login.
    # Por isso usuario_id pode ser NULL.
    # =====================================================

    usuario_id = db.Column(
        db.Integer,
        db.ForeignKey("usuario.id"),
        nullable=True,
        unique=True
    )

    usuario = db.relationship(
        "Usuario",
        foreign_keys=[usuario_id]
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
# PROGRAMAÇÃO SEMANAL
# =========================================================

class ProgramacaoSemanal(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    # 0 = segunda
    # 1 = terça
    # 2 = quarta
    # 3 = quinta
    # 4 = sexta
    # 5 = sábado
    # 6 = domingo

    dia_semana = db.Column(
        db.Integer,
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

    # =====================================================
    # USUÁRIO RESPONSÁVEL PELA RESERVA
    # =====================================================

    usuario_id = db.Column(
        db.Integer,
        db.ForeignKey("usuario.id"),
        nullable=True
    )

    usuario = db.relationship(
        "Usuario"
    )

    # =====================================================
    # DADOS DA RESERVA
    # =====================================================

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


# =========================================================
# USO DO CARRINHO
# =========================================================

class UsoCarrinho(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    reserva_id = db.Column(
        db.Integer,
        db.ForeignKey("reserva.id"),
        nullable=False
    )

    checkin_em = db.Column(
        db.DateTime,
        nullable=False,
        default=db.func.now()
    )

    checkout_em = db.Column(
        db.DateTime,
        nullable=True
    )

    reserva = db.relationship(
        "Reserva",
        backref="uso_carrinho"
    )


# =========================================================
# NOTIFICAÇÕES
# =========================================================

class Notificacao(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    usuario_id = db.Column(
        db.Integer,
        db.ForeignKey("usuario.id"),
        nullable=False
    )

    reserva_id = db.Column(
        db.Integer,
        db.ForeignKey("reserva.id"),
        nullable=True
    )

    titulo = db.Column(
        db.String(150),
        nullable=False
    )

    mensagem = db.Column(
        db.String(500),
        nullable=False
    )

    lida = db.Column(
        db.Boolean,
        default=False
    )

    criado_em = db.Column(
        db.DateTime,
        default=db.func.now()
    )

    usuario = db.relationship(
        "Usuario",
        backref="notificacoes"
    )

    reserva = db.relationship(
        "Reserva"
    )

# =========================================================
# HORÁRIO AVULSO
# Horário extra que vale só para uma data específica
# (não se repete toda semana).
# =========================================================

class HorarioAvulso(db.Model):

    __tablename__ = "horario_avulso"

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
# CAMPANHA
# Dia com programação própria (ex.: Finados).
# Nesse dia, a agenda mostra só os horários da campanha;
# a programação semanal normal não vale.
# =========================================================

class Campanha(db.Model):

    __tablename__ = "campanha"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    nome = db.Column(
        db.String(100),
        nullable=False
    )

    data = db.Column(
        db.Date,
        nullable=False,
        unique=True
    )

    # Local da campanha: todos os carrinhos ficam nele
    local_id = db.Column(
        db.Integer,
        db.ForeignKey("local.id"),
        nullable=True
    )

    local = db.relationship(
        "Local"
    )

    horarios = db.relationship(
        "CampanhaHorario",
        backref="campanha",
        cascade="all, delete-orphan",
        order_by="CampanhaHorario.hora_inicio"
    )


class CampanhaHorario(db.Model):

    __tablename__ = "campanha_horario"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    campanha_id = db.Column(
        db.Integer,
        db.ForeignKey("campanha.id"),
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

    hora_inicio = db.Column(
        db.Time,
        nullable=False
    )

    hora_fim = db.Column(
        db.Time,
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
# FOTO DE PERFIL (conteúdo da imagem)
# =========================================================

class FotoPessoa(db.Model):

    pessoa_id = db.Column(
        db.Integer,
        db.ForeignKey("pessoa.id"),
        primary_key=True
    )

    dados = db.Column(
        db.LargeBinary,
        nullable=False
    )

    tipo = db.Column(
        db.String(50),
        nullable=False
    )



# =========================================================
# TERRITÓRIOS
#
# Cada território tem uma área desenhada no mapa, quadras
# numeradas (com link de rota) e um histórico de designações
# (quem pegou, quando saiu, prazo e quando devolveu).
# =========================================================

class Territorio(db.Model):

    __tablename__ = "territorio"

    id = db.Column(db.Integer, primary_key=True)

    numero = db.Column(db.Integer, nullable=False, unique=True)

    nome = db.Column(db.String(150), nullable=False)

    # Residencial, Comercial ou Rural (opcional)
    etiqueta = db.Column(db.String(30), nullable=True)

    # Área no mapa em JSON: lista de polígonos, cada um uma
    # lista de pontos [lat, lng]. Ex.: [[[-22.7, -46.9], ...]]
    area = db.Column(db.Text, nullable=True)

    # Instruções que aparecem para quem for trabalhar
    observacoes = db.Column(db.Text, nullable=True)

    ativo = db.Column(db.Boolean, default=True, nullable=False)

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    quadras = db.relationship(
        "QuadraTerritorio",
        backref="territorio",
        cascade="all, delete-orphan",
        order_by="QuadraTerritorio.ordem"
    )

    designacoes = db.relationship(
        "DesignacaoTerritorio",
        backref="territorio",
        cascade="all, delete-orphan",
        order_by="DesignacaoTerritorio.data_saida.desc()"
    )

    @property
    def designacao_atual(self):
        for designacao in self.designacoes:
            if designacao.data_devolucao is None:
                return designacao
        return None

    @property
    def ultima_devolucao(self):
        datas = [
            d.data_devolucao for d in self.designacoes
            if d.data_devolucao is not None
        ]
        return max(datas) if datas else None

    def status(self, hoje):
        """disponivel, em_campo ou vencido."""
        atual = self.designacao_atual

        if atual is None:
            return "disponivel"

        if atual.prazo and atual.prazo < hoje:
            return "vencido"

        return "em_campo"


class QuadraTerritorio(db.Model):

    __tablename__ = "quadra_territorio"

    id = db.Column(db.Integer, primary_key=True)

    territorio_id = db.Column(
        db.Integer,
        db.ForeignKey("territorio.id"),
        nullable=False
    )

    ordem = db.Column(db.Integer, default=0, nullable=False)

    numero = db.Column(db.String(20), nullable=False)

    # Ex.: "Salão do Reino", "quadra pequena em frente à praça"
    descricao = db.Column(db.String(200), nullable=True)

    # Link do Google Maps para a quadra (opcional)
    link = db.Column(db.String(500), nullable=True)

    # Onde o número da quadra aparece no mapa (opcional)
    lat = db.Column(db.Float, nullable=True)
    lng = db.Column(db.Float, nullable=True)

    @property
    def link_rota(self):
        if self.link:
            return self.link
        if self.lat is not None and self.lng is not None:
            return (
                "https://www.google.com/maps/dir/?api=1&destination="
                f"{self.lat},{self.lng}"
            )
        return None


class DesignacaoTerritorio(db.Model):

    __tablename__ = "designacao_territorio"

    id = db.Column(db.Integer, primary_key=True)

    territorio_id = db.Column(
        db.Integer,
        db.ForeignKey("territorio.id"),
        nullable=False
    )

    # Dirigente escolhido da lista de pessoas
    pessoa_id = db.Column(
        db.Integer,
        db.ForeignKey("pessoa.id"),
        nullable=True
    )

    pessoa = db.relationship("Pessoa")

    # Nome guardado na hora da designação, para o histórico
    # continuar certo mesmo se a pessoa for excluída depois.
    dirigente_nome = db.Column(db.String(100), nullable=True)

    data_saida = db.Column(db.Date, nullable=False)

    prazo = db.Column(db.Date, nullable=True)

    data_devolucao = db.Column(db.Date, nullable=True)

    observacao = db.Column(db.String(300), nullable=True)

    criado_por_id = db.Column(
        db.Integer,
        db.ForeignKey("usuario.id"),
        nullable=True
    )

    @property
    def dirigente(self):
        if self.pessoa:
            return self.pessoa.nome
        return self.dirigente_nome or "—"


class DirigenteTerritorio(db.Model):
    """Pessoas que podem receber territórios (escolhidas em Ajustes)."""

    __tablename__ = "dirigente_territorio"

    pessoa_id = db.Column(
        db.Integer,
        db.ForeignKey("pessoa.id"),
        primary_key=True
    )

    pessoa = db.relationship("Pessoa")
