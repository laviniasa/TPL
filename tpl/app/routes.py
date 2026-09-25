from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash
)

from flask_login import (
    login_user,
    logout_user,
    login_required,
    current_user
)

from werkzeug.security import (
    check_password_hash,
    generate_password_hash
)

from app.models import (
    db,
    Usuario,
    Programacao,
    ProgramacaoSemanal,
    Reserva,
    ReservaParticipante,
    Carrinho,
    Local,
    Pessoa,
    UsoCarrinho
)


routes = Blueprint("routes", __name__)

SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def agora_sao_paulo():
    return datetime.now(SAO_PAULO)


@routes.after_request
def impedir_cache_da_agenda(response):
    # A Agenda autenticada não deve ficar disponível no cache
    # depois que o usuário fizer logout.
    if request.endpoint == "routes.agenda":
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

    return response


# =========================================================
# FUNÇÃO AUXILIAR
# =========================================================

def voltar_agenda_com_flash(data, mensagem, categoria="warning"):
    """
    Mostra a mensagem na própria agenda e evita abrir uma página
    intermediária de erro.
    """
    flash(mensagem, categoria)

    return redirect(
        url_for(
            "routes.agenda",
            data=data.isoformat()
        )
    )


def pessoas_da_reserva(reserva):

    nomes = [reserva.nome_principal]

    for participante in reserva.participantes:
        nomes.append(participante.nome)

    return nomes

def garantir_programacao_do_dia(data):
    """
    Garante que a programação de uma determinada data
    exista na tabela Programacao.

    A programação é criada a partir da regra semanal.
    """

    dia_semana = data.weekday()

    programacoes_semanais = (
        ProgramacaoSemanal.query
        .filter_by(dia_semana=dia_semana)
        .order_by(
            ProgramacaoSemanal.carrinho_id,
            ProgramacaoSemanal.hora_inicio
        )
        .all()
    )

    criadas = 0

    for semanal in programacoes_semanais:

        existe = Programacao.query.filter_by(
            data=data,
            hora_inicio=semanal.hora_inicio,
            hora_fim=semanal.hora_fim,
            carrinho_id=semanal.carrinho_id
        ).first()

        if existe:
            continue

        programacao = Programacao(
            data=data,
            hora_inicio=semanal.hora_inicio,
            hora_fim=semanal.hora_fim,
            carrinho_id=semanal.carrinho_id,
            local_id=semanal.local_id,
            quantidade_maxima=semanal.quantidade_maxima
        )

        db.session.add(programacao)
        criadas += 1

    if criadas:
        db.session.commit()
# =========================================================
# AGENDA
# =========================================================

@routes.route("/")
@login_required
def agenda():

    data_parametro = request.args.get("data")

    if data_parametro:
        data_selecionada = date.fromisoformat(
            data_parametro
        )
    else:
        data_selecionada = date.today()

    # Garante que os horários desta data existam
    garantir_programacao_do_dia(data_selecionada)
    # Segunda-feira da semana

    segunda = data_selecionada - timedelta(
        days=data_selecionada.weekday()
    )


    nomes_dias = [
        "SEG",
        "TER",
        "QUA",
        "QUI",
        "SEX",
        "SÁB",
        "DOM"
    ]


    dias_semana = []

    for i in range(7):

        dia = segunda + timedelta(days=i)

        dias_semana.append({
            "data": dia,
            "nome": nomes_dias[i]
        })


    # =====================================================
    # PROGRAMAÇÕES DO DIA
    # =====================================================

    programacoes = Programacao.query.filter_by(
        data=data_selecionada
    ).order_by(
        Programacao.carrinho_id,
        Programacao.hora_inicio
    ).all()


    # =====================================================
    # AGRUPAR POR CARRINHO
    # =====================================================

    carrinhos = {}


    for programacao in programacoes:

        numero = programacao.carrinho_id


        if numero not in carrinhos:

            carrinhos[numero] = {

                "carrinho": programacao.carrinho,

                "local": programacao.local,

                "horarios": []

            }


    # =====================================================
    # HORÁRIOS
    # =====================================================

    for numero, item in carrinhos.items():

        programacoes_carrinho = [

            p for p in programacoes

            if p.carrinho_id == numero

        ]


        for programacao in programacoes_carrinho:


            # -------------------------------------------------
            # RESERVAS DESTE CARRINHO/HORÁRIO
            # -------------------------------------------------

            reservas = Reserva.query.filter(
                Reserva.ativo == True,
                Reserva.carrinho_id == programacao.carrinho_id,
                Reserva.hora_inicio < programacao.hora_fim,
                Reserva.hora_fim > programacao.hora_inicio,
                db.or_(
                    Reserva.data_inicio == programacao.data,
                    db.and_(
                        Reserva.tipo == "FIXA",
                        Reserva.dia_semana == programacao.data.weekday(),
                        Reserva.data_inicio <= programacao.data
                    )
                )
            ).all()


            # -------------------------------------------------
            # NOMES
            # -------------------------------------------------

            nomes = []


            for reserva in reservas:

                nomes.extend(
                    pessoas_da_reserva(reserva)
                )


            total = len(nomes)


            vagas = max(

                programacao.quantidade_maxima
                - total,

                0

            )


            # -------------------------------------------------
            # QUANTOS CARRINHOS POSSUEM VAGA
            # -------------------------------------------------

            programacoes_mesmo_horario = Programacao.query.filter(

                Programacao.data
                == programacao.data,

                Programacao.hora_inicio
                < programacao.hora_fim,

                Programacao.hora_fim
                > programacao.hora_inicio

            ).all()


            carrinhos_disponiveis = 0


            for outra in programacoes_mesmo_horario:


                reservas_outra = Reserva.query.filter(

                    Reserva.data_inicio
                    == outra.data,

                    Reserva.carrinho_id
                    == outra.carrinho_id,

                    Reserva.ativo
                    == True,

                    Reserva.hora_inicio
                    < outra.hora_fim,

                    Reserva.hora_fim
                    > outra.hora_inicio

                ).all()


                total_outra = 0


                for reserva in reservas_outra:

                    total_outra += 1

                    total_outra += len(
                        reserva.participantes
                    )


                if total_outra < outra.quantidade_maxima:

                    carrinhos_disponiveis += 1


            # -------------------------------------------------
            # ADICIONAR HORÁRIO
            # -------------------------------------------------

            item["horarios"].append({

                "programacao": programacao,

                "nomes": nomes,

                "total": total,

                "vagas": vagas,

                "reservas": reservas,

                # IMPORTANTE:
                # o agenda.html usa horario.reserva

                "reserva": (
                    reservas[0]
                    if reservas
                    else None
                ),

                "carrinhos_disponiveis":
                    carrinhos_disponiveis

            })


    carrinhos_lista = list(
        carrinhos.values()
    )


    # =====================================================
    # RESERVAS PARA O CALENDÁRIO
    # =====================================================

    reservas_calendario = Reserva.query.filter(
        Reserva.ativo == True
    ).all()

    dados_calendario = []

    for reserva in reservas_calendario:

        dados_calendario.append({
            "id": reserva.id,
            "tipo": reserva.tipo,
            "data_inicio": reserva.data_inicio.isoformat(),
            "dia_semana": reserva.dia_semana,
            "hora_inicio": reserva.hora_inicio.strftime("%H:%M"),
            "hora_fim": reserva.hora_fim.strftime("%H:%M"),
            "carrinho": reserva.carrinho.nome,
            "local": reserva.local.nome,
            "responsavel": reserva.nome_principal,
            "participantes": [
                participante.nome
                for participante in reserva.participantes
            ]
        })


    return render_template(
        "agenda.html",

        carrinhos=carrinhos_lista,

        dias_semana=dias_semana,

        data_selecionada=data_selecionada,

        hoje=agora_sao_paulo().date(),

        segunda=segunda,

        timedelta=timedelta,

        reservas_calendario=dados_calendario
    )

# =========================================================
# RESERVAR HORÁRIO
# =========================================================
@routes.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("routes.agenda"))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        senha = request.form.get("senha", "")

        usuario = Usuario.query.filter_by(
            email=email
        ).first()

        if usuario and check_password_hash(usuario.senha, senha):
            if not usuario.ativo:
                flash("Este usuário está inativo.")
                return redirect(url_for("routes.login"))

            login_user(usuario)

            proxima = request.args.get("next")

            if proxima:
                return redirect(proxima)

            return redirect(url_for("routes.agenda"))

        flash("E-mail ou senha incorretos.")

    return render_template("login.html")


@routes.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("routes.login"))

@routes.route(
    "/participar/<int:programacao_id>",
    methods=["GET", "POST"]
)
@login_required
def participar(programacao_id):

    programacao = Programacao.query.get_or_404(
        programacao_id
    )

    # =====================================================
    # IMPEDIR RESERVA PARA DATA QUE JÁ PASSOU
    # =====================================================

    hoje = agora_sao_paulo().date()

    if programacao.data < hoje:
        return voltar_agenda_com_flash(
            hoje,
            "Não é possível fazer uma reserva para uma data que já passou.",
            "warning"
        )


    # Lista provisória de irmãos

    pessoas = Pessoa.query.filter_by(
        ativo=True
    ).order_by(
        Pessoa.nome
    ).all()


    if request.method == "POST":


        # -------------------------------------------------
        # NOME PRINCIPAL
        # -------------------------------------------------

        nome_principal = request.form.get(
            "nome_principal",
            ""
        ).strip()


        selecionados = request.form.getlist(
            "pessoas"
        )

        reserva_fixa = request.form.get(
            "reserva_fixa"
        ) == "sim"


        if not nome_principal:
            return voltar_agenda_com_flash(
                programacao.data,
                "Informe seu nome para continuar.",
                "warning"
            )


        # -------------------------------------------------
        # LIMITE DE PARTICIPANTES
        # -------------------------------------------------

        if len(selecionados) > 2:
            return voltar_agenda_com_flash(
                programacao.data,
                "Você pode escolher no máximo 2 irmãos além de você.",
                "warning"
            )


        # -------------------------------------------------
        # VERIFICAR NOMES REPETIDOS NA PRÓPRIA RESERVA
        # -------------------------------------------------

        nomes_nova_reserva = [

            nome_principal

        ] + selecionados


        nomes_normalizados = [

            nome.strip().lower()

            for nome in nomes_nova_reserva

        ]


        if len(nomes_normalizados) != len(set(nomes_normalizados)):
            return voltar_agenda_com_flash(
                programacao.data,
                "A mesma pessoa não pode ser selecionada duas vezes.",
                "warning"
            )


        # -------------------------------------------------
        # TODAS AS RESERVAS DO MESMO HORÁRIO
        # -------------------------------------------------

        reservas = Reserva.query.filter(

            Reserva.data_inicio
            == programacao.data,

            Reserva.ativo
            == True,

            Reserva.hora_inicio
            < programacao.hora_fim,

            Reserva.hora_fim
            > programacao.hora_inicio

        ).all()


        # -------------------------------------------------
        # PESSOAS QUE JÁ ESTÃO EM OUTRO CARRINHO
        # -------------------------------------------------

        pessoas_ocupadas = []


        for reserva in reservas:

            pessoas_ocupadas.extend(
                pessoas_da_reserva(reserva)
            )


        pessoas_ocupadas_normalizadas = [

            nome.strip().lower()

            for nome in pessoas_ocupadas

        ]


        # -------------------------------------------------
        # IMPEDIR A MESMA PESSOA EM OUTRO CARRINHO
        # -------------------------------------------------

        for nome in nomes_nova_reserva:

            if (
                nome.strip().lower()
                in pessoas_ocupadas_normalizadas
            ):

                return voltar_agenda_com_flash(
                    programacao.data,
                    f"{nome} já está participando de outro carrinho neste horário.",
                    "warning"
                )


        # -------------------------------------------------
        # CAPACIDADE DESTE CARRINHO
        # -------------------------------------------------

        reservas_carrinho = Reserva.query.filter(

            Reserva.data_inicio
            == programacao.data,

            Reserva.carrinho_id
            == programacao.carrinho_id,

            Reserva.ativo
            == True,

            Reserva.hora_inicio
            < programacao.hora_fim,

            Reserva.hora_fim
            > programacao.hora_inicio

        ).all()


        pessoas_no_carrinho = 0


        for reserva in reservas_carrinho:

            pessoas_no_carrinho += 1

            pessoas_no_carrinho += len(
                reserva.participantes
            )


        quantidade_nova = len(
            nomes_nova_reserva
        )


        if (
            pessoas_no_carrinho
            + quantidade_nova
            > programacao.quantidade_maxima
        ):

            return voltar_agenda_com_flash(
                programacao.data,
                "Este carrinho não possui vagas suficientes.",
                "danger"
            )


        # -------------------------------------------------
        # CRIAR RESERVA
        # -------------------------------------------------

        if reserva_fixa:
            tipo_reserva = "FIXA"
            data_fim_reserva = None
            dia_semana_reserva = programacao.data.weekday()
        else:
            tipo_reserva = "UNICA"
            data_fim_reserva = programacao.data
            dia_semana_reserva = None


        reserva = Reserva(
            usuario_id=current_user.id,
            nome_principal=nome_principal,
            tipo=tipo_reserva,
            data_inicio=programacao.data,
            data_fim=data_fim_reserva,
            dia_semana=dia_semana_reserva,
            hora_inicio=programacao.hora_inicio,
            hora_fim=programacao.hora_fim,
            carrinho_id=programacao.carrinho_id,
            local_id=programacao.local_id,
            ativo=True
        )


        db.session.add(reserva)

        db.session.flush()


        # -------------------------------------------------
        # PARTICIPANTES
        # -------------------------------------------------

        for nome in selecionados:

            participante = ReservaParticipante(

                reserva_id=reserva.id,

                nome=nome

            )

            db.session.add(
                participante
            )


        db.session.commit()

        flash(
            f"Reserva criada para {programacao.hora_inicio.strftime('%H:%M')} às {programacao.hora_fim.strftime('%H:%M')}.",
            "success"
        )

        return redirect(
            url_for(
                "routes.agenda",
                data=programacao.data.isoformat()
            )
        )

    reserva_fixa_existente = Reserva.query.filter(
        Reserva.tipo == "FIXA",
        Reserva.ativo == True,
        Reserva.carrinho_id == programacao.carrinho_id,
        Reserva.dia_semana == programacao.data.weekday(),
        Reserva.hora_inicio == programacao.hora_inicio,
        Reserva.hora_fim == programacao.hora_fim,
        Reserva.data_inicio <= programacao.data
    ).first()

    return render_template(
        "participar.html",
        programacao=programacao,
        pessoas=pessoas,
        reserva_fixa_existente=reserva_fixa_existente
    )


# =========================================================
# EXCLUIR RESERVA
# =========================================================

@routes.route(
    "/excluir-reserva/<int:reserva_id>",
    methods=["POST"]
)
@login_required
def excluir_reserva(reserva_id):

    reserva = Reserva.query.get_or_404(reserva_id)

    if current_user.tipo != "ADMIN" and reserva.usuario_id != current_user.id:
        flash("Você não tem permissão para excluir esta reserva.", "danger")
        return redirect(url_for("routes.agenda", data=date.today().isoformat()))

    data = reserva.data_inicio

    # Não apagamos fisicamente.
    # Apenas desativamos a reserva.

    reserva.ativo = False


    db.session.commit()

    flash("Reserva excluída com sucesso.", "success")

    return redirect(
        url_for(
            "routes.agenda",
            data=data.isoformat()
        )
    )

# =========================================================
# CANCELAR RESERVA FIXA
# =========================================================

@routes.route(
    "/cancelar-reserva-fixa/<int:reserva_id>",
    methods=["POST"]
)
def cancelar_reserva_fixa(reserva_id):

    reserva = Reserva.query.get_or_404(
        reserva_id
    )

    # Verifica se realmente é uma reserva fixa
    if reserva.tipo != "FIXA":
        return voltar_agenda_com_flash(
            date.today(),
            "Esta não é uma reserva fixa.",
            "danger"
        )

    # Cancela a reserva fixa
    reserva.ativo = False

    db.session.commit()

    return redirect(
        url_for(
            "routes.agenda",
            data=date.today().isoformat()
        )
    )


# =========================================================
# NOVA RESERVA
# =========================================================

@routes.route("/nova-reserva")
def nova_reserva():

    carrinhos = Carrinho.query.filter_by(
        ativo=True
    ).all()


    locais = Local.query.filter_by(
        ativo=True
    ).all()


    programacao_id = request.args.get(
        "programacao_id"
    )


    programacao = None


    if programacao_id:

        programacao = Programacao.query.get_or_404(

            int(programacao_id)

        )


    return render_template(

        "nova_reserva.html",

        carrinhos=carrinhos,

        locais=locais,

        programacao=programacao

    )

# =========================================================
# ÁREA ADMINISTRATIVA
# =========================================================

@routes.route("/admin")
def admin():

    return render_template(
        "admin.html"
    )


# =========================================================
# ADMIN - CARRINHOS
# =========================================================

@routes.route("/admin/carrinhos")
def admin_carrinhos():

    carrinhos = (
        Carrinho.query
        .order_by(Carrinho.id)
        .all()
    )

    return render_template(
        "admin_carrinhos.html",
        carrinhos=carrinhos
    )


@routes.route(
    "/admin/carrinhos/adicionar",
    methods=["POST"]
)
def adicionar_carrinho():

    nome = request.form.get(
        "nome",
        ""
    ).strip()

    if not nome:
        return "Informe o nome do carrinho.", 400

    carrinho_existente = (
        Carrinho.query
        .filter_by(nome=nome)
        .first()
    )

    if carrinho_existente:
        return "Já existe um carrinho com esse nome.", 400

    carrinho = Carrinho(
        nome=nome,
        ativo=True
    )

    db.session.add(carrinho)
    db.session.commit()

    return redirect(
        url_for("routes.admin_carrinhos")
    )


@routes.route(
    "/admin/carrinhos/editar/<int:carrinho_id>",
    methods=["POST"]
)
def editar_carrinho(carrinho_id):

    carrinho = Carrinho.query.get_or_404(
        carrinho_id
    )

    nome = request.form.get(
        "nome",
        ""
    ).strip()

    if not nome:
        return "Informe o nome do carrinho.", 400

    outro_carrinho = (
        Carrinho.query
        .filter(
            Carrinho.nome == nome,
            Carrinho.id != carrinho.id
        )
        .first()
    )

    if outro_carrinho:
        return "Já existe um carrinho com esse nome.", 400

    carrinho.nome = nome

    db.session.commit()

    return redirect(
        url_for("routes.admin_carrinhos")
    )


@routes.route(
    "/admin/carrinhos/alternar/<int:carrinho_id>",
    methods=["POST"]
)
def alternar_carrinho(carrinho_id):

    carrinho = Carrinho.query.get_or_404(
        carrinho_id
    )

    carrinho.ativo = not carrinho.ativo

    db.session.commit()

    return redirect(
        url_for("routes.admin_carrinhos")
    )


@routes.route("/admin/pessoas")
def admin_pessoas():
    pessoas = Pessoa.query.order_by(Pessoa.nome).all()

    return render_template(
        "admin_pessoas.html",
        pessoas=pessoas
    )


@routes.route(
    "/admin/pessoas/adicionar",
    methods=["POST"]
)
def adicionar_pessoa():

    nome = request.form.get(
        "nome",
        ""
    ).strip()

    valor_acesso = request.form.get(
        "permitir_acesso",
        ""
    ).strip().lower()

    permitir_acesso = valor_acesso in [
        "sim",
        "on",
        "true",
        "1",
        "yes"
    ]

    email = request.form.get(
        "email",
        ""
    ).strip().lower()

    senha = request.form.get(
        "senha",
        ""
    )

    # -------------------------------------------------
    # VALIDAR NOME
    # -------------------------------------------------

    if not nome:
        return "Informe o nome do irmão.", 400

    pessoa_existente = (
        Pessoa.query
        .filter_by(nome=nome)
        .first()
    )

    if pessoa_existente:
        return "Esta pessoa já está cadastrada.", 400

    # -------------------------------------------------
    # VALIDAR ACESSO AO TPL
    # -------------------------------------------------

    if permitir_acesso:

        if not email:
            return (
                "Informe o e-mail para criar o acesso ao TPL.",
                400
            )

        if not senha:
            return (
                "Informe uma senha inicial para criar o acesso.",
                400
            )

        usuario_existente = (
            Usuario.query
            .filter_by(email=email)
            .first()
        )

        if usuario_existente:
            return (
                "Este e-mail já está cadastrado.",
                400
            )

    # -------------------------------------------------
    # CRIAR PESSOA
    # -------------------------------------------------

    pessoa = Pessoa(
        nome=nome,
        ativo=True
    )

    db.session.add(pessoa)

    # -------------------------------------------------
    # CRIAR LOGIN, SE SOLICITADO
    # -------------------------------------------------

    if permitir_acesso:

        usuario = Usuario(
            nome=nome,
            email=email,
            senha=generate_password_hash(senha),
            tipo="IRMAO",
            ativo=True
        )

        db.session.add(usuario)

        # Obtém o ID do usuário antes de associá-lo à pessoa.
        db.session.flush()

        pessoa.usuario_id = usuario.id

    db.session.commit()

    return redirect(
        url_for("routes.admin_pessoas")
    )


@routes.route(
    "/admin/pessoas/editar/<int:pessoa_id>",
    methods=["POST"]
)
def editar_pessoa(pessoa_id):

    pessoa = Pessoa.query.get_or_404(
        pessoa_id
    )

    # =====================================================
    # DADOS DO FORMULÁRIO
    # =====================================================

    nome = request.form.get(
        "nome",
        ""
    ).strip()

    valor_acesso = request.form.get(
        "permitir_acesso",
        ""
    ).strip().lower()

    permitir_acesso = valor_acesso in [
        "sim",
        "on",
        "true",
        "1",
        "yes"
    ]

    email = request.form.get(
        "email",
        ""
    ).strip().lower()

    senha = request.form.get(
        "senha",
        ""
    ).strip()

    tipo = request.form.get(
        "tipo",
        "IRMAO"
    ).strip().upper()

    # =====================================================
    # VALIDAR NOME
    # =====================================================

    if not nome:
        flash(
            "Informe o nome do irmão.",
            "warning"
        )
        return redirect(
            url_for("routes.admin_pessoas")
        )

    outra_pessoa = (
        Pessoa.query
        .filter(
            Pessoa.nome == nome,
            Pessoa.id != pessoa.id
        )
        .first()
    )

    if outra_pessoa:
        flash(
            "Já existe uma pessoa com esse nome.",
            "warning"
        )
        return redirect(
            url_for("routes.admin_pessoas")
        )

    pessoa.nome = nome

    # =====================================================
    # ACESSO AO TPL ATIVADO
    # =====================================================

    if permitir_acesso:

        if not email:
            flash(
                "Informe o e-mail para permitir o acesso ao TPL.",
                "warning"
            )
            return redirect(
                url_for("routes.admin_pessoas")
            )

        if tipo not in ["IRMAO", "ADMIN"]:
            tipo = "IRMAO"

        usuario_atual_id = (
            pessoa.usuario.id
            if pessoa.usuario
            else None
        )

        usuario_com_email = (
            Usuario.query
            .filter(
                Usuario.email == email,
                Usuario.id != usuario_atual_id
            )
            .first()
        )

        if usuario_com_email:
            flash(
                "Este e-mail já está sendo usado por outro usuário.",
                "warning"
            )
            return redirect(
                url_for("routes.admin_pessoas")
            )

        # Pessoa já possui login
        if pessoa.usuario:

            usuario = pessoa.usuario

            usuario.nome = nome
            usuario.email = email
            usuario.tipo = tipo
            usuario.ativo = True

            # Só troca a senha se uma nova senha foi informada
            if senha:
                usuario.senha = generate_password_hash(
                    senha
                )

        # Pessoa ainda não possui login
        else:

            if not senha:
                flash(
                    "Informe uma senha para criar o acesso ao TPL.",
                    "warning"
                )
                return redirect(
                    url_for("routes.admin_pessoas")
                )

            usuario = Usuario(
                nome=nome,
                email=email,
                senha=generate_password_hash(senha),
                tipo=tipo,
                ativo=True
            )

            db.session.add(usuario)
            db.session.flush()

            # Liga a pessoa ao usuário
            pessoa.usuario_id = usuario.id

    # =====================================================
    # ACESSO DESMARCADO
    # =====================================================

    else:

        if pessoa.usuario:
            # Mantém o usuário no banco, mas impede o login
            pessoa.usuario.ativo = False

    # =====================================================
    # SALVAR
    # =====================================================

    db.session.commit()

    flash(
        f"{pessoa.nome} foi atualizado com sucesso.",
        "success"
    )

    return redirect(
        url_for("routes.admin_pessoas")
    )


@routes.route(
    "/admin/pessoas/alternar/<int:pessoa_id>",
    methods=["POST"]
)
def alternar_pessoa(pessoa_id):

    pessoa = Pessoa.query.get_or_404(
        pessoa_id
    )

    pessoa.ativo = not pessoa.ativo

    db.session.commit()

    return redirect(
        url_for("routes.admin_pessoas")
    )

# =========================================================
# ADMIN - EXCLUIR PESSOA
# =========================================================

@routes.route(
    "/admin/pessoas/excluir/<int:pessoa_id>",
    methods=["POST"]
)
def excluir_pessoa(pessoa_id):

    pessoa = Pessoa.query.get_or_404(
        pessoa_id
    )

    nome = pessoa.nome

    # =====================================================
    # USUÁRIO VINCULADO
    # =====================================================

    usuario = pessoa.usuario


    # =====================================================
    # VERIFICAR SE O USUÁRIO JÁ POSSUI RESERVAS
    # =====================================================

    possui_reservas = False

    if usuario:

        possui_reservas = (
            Reserva.query
            .filter_by(
                usuario_id=usuario.id
            )
            .first()
            is not None
        )


    # =====================================================
    # EXCLUIR A PESSOA
    # =====================================================

    # Remove o vínculo antes de apagar a pessoa.
    if usuario:

        pessoa.usuario_id = None

        db.session.flush()


    db.session.delete(pessoa)

    db.session.flush()


    # =====================================================
    # USUÁRIO
    # =====================================================

    if usuario:

        if possui_reservas:

            # -------------------------------------------------
            # A pessoa já possui reservas históricas.
            #
            # Não podemos apagar o usuário porque essas
            # reservas precisam continuar apontando para ele.
            #
            # Então mantemos o usuário no banco, mas
            # desativamos o acesso.
            # -------------------------------------------------

            usuario.ativo = False

        else:

            # -------------------------------------------------
            # Nunca fez reserva.
            #
            # Nesse caso podemos excluir também o usuário.
            # -------------------------------------------------

            db.session.delete(usuario)


    # =====================================================
    # SALVAR
    # =====================================================

    try:

        db.session.commit()

    except Exception:

        db.session.rollback()

        flash(
            "Não foi possível excluir esta pessoa. "
            "Verifique se ela possui registros vinculados.",
            "danger"
        )

        return redirect(
            url_for("routes.admin_pessoas")
        )


    flash(
        f"{nome} foi excluído com sucesso.",
        "success"
    )

    return redirect(
        url_for("routes.admin_pessoas")
    )

# =========================================================
# ADMIN - LOCAIS
# =========================================================

@routes.route("/admin/locais")
def admin_locais():

    locais = (
        Local.query
        .order_by(Local.nome)
        .all()
    )

    return render_template(
        "admin_locais.html",
        locais=locais
    )


@routes.route(
    "/admin/locais/adicionar",
    methods=["POST"]
)
def adicionar_local():

    nome = request.form.get(
        "nome",
        ""
    ).strip()

    if not nome:
        return "Informe o nome do local.", 400

    local_existente = (
        Local.query
        .filter_by(nome=nome)
        .first()
    )

    if local_existente:
        return "Já existe um local com esse nome.", 400

    local = Local(
        nome=nome,
        ativo=True
    )

    db.session.add(local)
    db.session.commit()

    return redirect(
        url_for("routes.admin_locais")
    )


@routes.route(
    "/admin/locais/editar/<int:local_id>",
    methods=["POST"]
)
def editar_local(local_id):

    local = Local.query.get_or_404(
        local_id
    )

    nome = request.form.get(
        "nome",
        ""
    ).strip()

    if not nome:
        return "Informe o nome do local.", 400

    outro_local = (
        Local.query
        .filter(
            Local.nome == nome,
            Local.id != local.id
        )
        .first()
    )

    if outro_local:
        return "Já existe um local com esse nome.", 400

    local.nome = nome

    db.session.commit()

    return redirect(
        url_for("routes.admin_locais")
    )

# =========================================================
# ADMIN - EXCLUIR LOCAL
# =========================================================

@routes.route(
    "/admin/locais/excluir/<int:local_id>",
    methods=["POST"]
)
def excluir_local(local_id):

    local = Local.query.get_or_404(
        local_id
    )

    # Verifica se o local está sendo usado
    # em alguma programação semanal
    uso_programacao_semanal = (
        ProgramacaoSemanal.query
        .filter_by(local_id=local.id)
        .first()
    )

    # Verifica se o local está sendo usado
    # em alguma programação de uma data
    uso_programacao = (
        Programacao.query
        .filter_by(local_id=local.id)
        .first()
    )

    # Verifica se o local está sendo usado
    # em alguma reserva
    uso_reserva = (
        Reserva.query
        .filter_by(local_id=local.id)
        .first()
    )

    if (
        uso_programacao_semanal
        or uso_programacao
        or uso_reserva
    ):
        return (
            "Este local já está sendo utilizado "
            "em uma programação ou reserva e "
            "não pode ser excluído."
        ), 400

    # Se nunca foi utilizado, pode excluir
    db.session.delete(local)
    db.session.commit()

    return redirect(
        url_for("routes.admin_locais")
    )

# =========================================================
# CHECK-IN DO CARRINHO
# =========================================================

@routes.route("/checkin/<int:reserva_id>", methods=["POST"])
@login_required
def checkin(reserva_id):

    reserva = Reserva.query.get_or_404(reserva_id)

    # O responsável pela reserva pode fazer CHECK-IN.
    # ADMIN também pode.
    if (
        current_user.tipo != "ADMIN"
        and reserva.usuario_id != current_user.id
    ):
        flash("Você não tem permissão para fazer o check-in desta reserva.", "danger")
        return redirect(url_for("routes.agenda", data=date.today().isoformat()))

    if not reserva.ativo:
        flash("Esta reserva não está mais ativa.", "danger")
        return redirect(url_for("routes.agenda", data=date.today().isoformat()))

    agora = agora_sao_paulo()
    hoje = agora.date()

    if reserva.tipo == "FIXA":

        if hoje < reserva.data_inicio:
            flash("O check-in ainda não está disponível.", "warning")
            return redirect(url_for("routes.agenda", data=hoje.isoformat()))

        if reserva.dia_semana != hoje.weekday():
            flash("O check-in só está disponível no dia programado para esta reserva.", "warning")
            return redirect(url_for("routes.agenda", data=hoje.isoformat()))

        data_ocorrencia = hoje

    else:

        if hoje < reserva.data_inicio:
            flash(
                f"O check-in ainda não está disponível. Seu horário começa em {reserva.data_inicio.strftime('%d/%m/%Y')} às {reserva.hora_inicio.strftime('%H:%M')}.",
                "warning"
            )
            return redirect(url_for("routes.agenda", data=reserva.data_inicio.isoformat()))

        if hoje > reserva.data_inicio:
            flash("O horário desta reserva já passou.", "warning")
            return redirect(url_for("routes.agenda", data=hoje.isoformat()))

        data_ocorrencia = reserva.data_inicio

    inicio = datetime.combine(
        data_ocorrencia,
        reserva.hora_inicio
    )

    fim = datetime.combine(
        data_ocorrencia,
        reserva.hora_fim
    )

    if agora < inicio:
        flash(
            f"O check-in ainda não está disponível. Seu horário começa às {reserva.hora_inicio.strftime('%H:%M')}.",
            "warning"
        )
        return redirect(url_for("routes.agenda", data=data_ocorrencia.isoformat()))

    if agora >= fim:
        flash("O horário desta reserva já terminou. O check-in não está mais disponível.", "warning")
        return redirect(url_for("routes.agenda", data=data_ocorrencia.isoformat()))

    uso_existente = UsoCarrinho.query.filter_by(
        reserva_id=reserva.id,
        checkout_em=None
    ).first()

    if uso_existente:
        flash("Esta reserva já está com o carrinho em uso.", "warning")
        return redirect(url_for("routes.agenda", data=data_ocorrencia.isoformat()))

    uso_outro = (
        UsoCarrinho.query
        .join(Reserva)
        .filter(
            Reserva.carrinho_id == reserva.carrinho_id,
            Reserva.ativo == True,
            UsoCarrinho.checkout_em == None,
            UsoCarrinho.reserva_id != reserva.id
        )
        .first()
    )

    if uso_outro:
        flash("Este carrinho está em uso no momento. Faça o check-out quando devolvê-lo.", "warning")
        return redirect(url_for("routes.agenda", data=data_ocorrencia.isoformat()))

    uso = UsoCarrinho(
        reserva_id=reserva.id,
        checkin_em=agora
    )

    db.session.add(uso)
    db.session.commit()

    flash(
        "Sua presença foi confirmada. Check-in realizado com sucesso!",
        "success"
    )

    return redirect(
        url_for(
            "routes.agenda",
            data=data_ocorrencia.isoformat()
        )
    )


# =========================================================
# CHECK-OUT DO CARRINHO
# =========================================================

@routes.route("/checkout/<int:reserva_id>", methods=["POST"])
@login_required
def checkout(reserva_id):

    reserva = Reserva.query.get_or_404(reserva_id)

    # O responsável pela reserva pode fazer CHECK-OUT.
    # ADMIN também pode.
    if (
        current_user.tipo != "ADMIN"
        and reserva.usuario_id != current_user.id
    ):
        flash("Você não tem permissão para fazer o check-out desta reserva.", "danger")
        return redirect(url_for("routes.agenda", data=date.today().isoformat()))

    uso = (
        UsoCarrinho.query
        .filter_by(
            reserva_id=reserva.id,
            checkout_em=None
        )
        .first()
    )

    if not uso:
        flash("Não existe um check-in ativo para esta reserva.", "warning")
        return redirect(url_for("routes.agenda", data=date.today().isoformat()))

    uso.checkout_em = agora_sao_paulo()

    # Reserva única termina após a devolução.
    # Reserva fixa continua ativa para a próxima semana.
    if reserva.tipo != "FIXA":
        reserva.ativo = False

    db.session.commit()

    flash("Check-out realizado. Obrigado por devolver o carrinho!", "success")

    data_retorno = (
        date.today()
        if reserva.tipo == "FIXA"
        else reserva.data_inicio
    )

    return redirect(
        url_for(
            "routes.agenda",
            data=data_retorno.isoformat()
        )
    )


@routes.route("/admin/programacao")
def admin_programacao():
    programacoes = (
        ProgramacaoSemanal.query
        .order_by(
            ProgramacaoSemanal.dia_semana,
            ProgramacaoSemanal.hora_inicio
        )
        .all()
    )

    dias = {
        0: "SEGUNDA-FEIRA",
        1: "TERÇA-FEIRA",
        2: "QUARTA-FEIRA",
        3: "QUINTA-FEIRA",
        4: "SEXTA-FEIRA",
        5: "SÁBADO",
        6: "DOMINGO"
    }

    programacao_por_dia = {}

    for programacao in programacoes:
        if programacao.dia_semana not in programacao_por_dia:
            programacao_por_dia[programacao.dia_semana] = []

        programacao_por_dia[programacao.dia_semana].append(
            programacao
        )

    return render_template(
        "admin_programacao.html",
        programacao_por_dia=programacao_por_dia,
        dias=dias,
        carrinhos=Carrinho.query.filter_by(ativo=True).order_by(Carrinho.id).all(),
        locais=Local.query.filter_by(ativo=True).order_by(Local.nome).all()
    )

@routes.route("/admin/programacao/editar/<int:programacao_id>", methods=["POST"])
def editar_programacao(programacao_id):
    programacao = ProgramacaoSemanal.query.get_or_404(programacao_id)

    carrinho_id = request.form.get("carrinho_id")
    local_id = request.form.get("local_id")
    hora_inicio = request.form.get("hora_inicio")
    hora_fim = request.form.get("hora_fim")

    if not carrinho_id or not local_id or not hora_inicio or not hora_fim:
        return "Preencha todos os campos.", 400

    try:
        inicio = datetime.strptime(hora_inicio, "%H:%M").time()
        fim = datetime.strptime(hora_fim, "%H:%M").time()
    except ValueError:
        return "Horário inválido.", 400

    if inicio >= fim:
        return "O horário de início deve ser anterior ao horário de fim.", 400

    programacao.carrinho_id = int(carrinho_id)
    programacao.local_id = int(local_id)
    programacao.hora_inicio = inicio
    programacao.hora_fim = fim

    db.session.commit()

    return redirect(url_for("routes.admin_programacao"))

    # =========================================================
# ADMIN - RESERVAS
# =========================================================

@routes.route("/admin/reservas")
def admin_reservas():

    # Reservas fixas que aparecem na tela principal
    reservas = Reserva.query.filter(
        Reserva.tipo == "FIXA",
        Reserva.ativo == True
    ).order_by(
        Reserva.dia_semana,
        Reserva.hora_inicio
    ).all()

    # Todas as reservas ativas que serão usadas pelo calendário
    reservas_calendario = Reserva.query.filter(
        Reserva.ativo == True
    ).all()

    dados_calendario = []

    for reserva in reservas_calendario:

        dados_calendario.append({
            "id": reserva.id,
            "tipo": reserva.tipo,
            "data_inicio": reserva.data_inicio.isoformat(),
            "dia_semana": reserva.dia_semana,
            "hora_inicio": reserva.hora_inicio.strftime("%H:%M"),
            "hora_fim": reserva.hora_fim.strftime("%H:%M"),
            "carrinho": reserva.carrinho.nome,
            "local": reserva.local.nome,
            "responsavel": reserva.nome_principal,
            "participantes": [
                participante.nome
                for participante in reserva.participantes
            ]
        })

    return render_template(
        "admin_reservas.html",
        reservas=reservas,
        reservas_calendario=dados_calendario
    )