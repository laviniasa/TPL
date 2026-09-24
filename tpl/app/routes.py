from datetime import date, datetime, timedelta
from werkzeug.security import (
    check_password_hash,
    generate_password_hash
)

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    current_app
)

from flask_login import (
    login_user,
    logout_user,
    login_required,
    current_user
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

# =========================================================
# PROTEÇÃO DA ÁREA ADMINISTRATIVA
# =========================================================

@routes.before_request
def proteger_administracao():

    if request.path.startswith("/admin"):

        # Se não estiver logado, vai para o login
        if not current_user.is_authenticated:
            return redirect(
                url_for(
                    "routes.login",
                    next=request.full_path
                )
            )

        # Se estiver logado, mas não for administrador
        if current_user.tipo != "ADMIN":
            return "Acesso permitido somente para administradores.", 403


@routes.route("/service-worker.js")
def service_worker():
    """Entrega o Service Worker pela raiz do site para o PWA."""
    resposta = current_app.send_static_file("service-worker.js")
    resposta.headers["Service-Worker-Allowed"] = "/"
    resposta.headers["Cache-Control"] = "no-cache"
    return resposta


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
def participar(programacao_id):

    programacao = Programacao.query.get_or_404(
        programacao_id
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

            return "Informe seu nome."


        # -------------------------------------------------
        # LIMITE DE PARTICIPANTES
        # -------------------------------------------------

        if len(selecionados) > 2:

            return (
                "Você pode escolher no máximo "
                "2 irmãos."
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


        if len(nomes_normalizados) != len(
            set(nomes_normalizados)
        ):

            return (
                "A mesma pessoa não pode "
                "ser selecionada duas vezes."
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

                return (
                    f"{nome} já está participando "
                    "de outro carrinho neste horário."
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

            return (
                "Este carrinho não possui "
                "vagas suficientes."
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
def excluir_reserva(reserva_id):

    reserva = Reserva.query.get_or_404(
        reserva_id
    )


    data = reserva.data_inicio


    # Não apagamos fisicamente.
    # Apenas desativamos a reserva.

    reserva.ativo = False


    db.session.commit()


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
        return "Esta não é uma reserva fixa.", 400

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
# ADMIN - LOCAIS
# =========================================================

@routes.route("/admin/locais")
@login_required
def admin_locais():

    if current_user.tipo != "ADMIN":
        return "Acesso permitido somente para administradores.", 403

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
@login_required
def adicionar_local():

    if current_user.tipo != "ADMIN":
        return "Acesso permitido somente para administradores.", 403

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
@login_required
def editar_local(local_id):

    if current_user.tipo != "ADMIN":
        return "Acesso permitido somente para administradores.", 403

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


@routes.route(
    "/admin/locais/alternar/<int:local_id>",
    methods=["POST"]
)
@login_required
def alternar_local(local_id):

    if current_user.tipo != "ADMIN":
        return "Acesso permitido somente para administradores.", 403

    local = Local.query.get_or_404(
        local_id
    )

    local.ativo = not local.ativo

    db.session.commit()

    return redirect(
        url_for("routes.admin_locais")
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

# =========================================================
# ADMIN - PESSOAS
# =========================================================

@routes.route("/admin/pessoas")
@login_required
def admin_pessoas():

    if current_user.tipo != "ADMIN":
        return "Acesso permitido somente para administradores.", 403

    pessoas = Pessoa.query.order_by(
        Pessoa.nome
    ).all()

    return render_template(
        "admin_pessoas.html",
        pessoas=pessoas
    )


@routes.route(
    "/admin/pessoas/adicionar",
    methods=["POST"]
)
@login_required
def adicionar_pessoa():

    if current_user.tipo != "ADMIN":
        return "Acesso permitido somente para administradores.", 403

    # -----------------------------------------------------
    # DADOS DA PESSOA
    # -----------------------------------------------------

    nome = request.form.get(
        "nome",
        ""
    ).strip()

    if not nome:
        return "Informe o nome do irmão.", 400

    pessoa_existente = Pessoa.query.filter_by(
        nome=nome
    ).first()

    if pessoa_existente:
        return "Esta pessoa já está cadastrada.", 400

    pessoa = Pessoa(
        nome=nome,
        ativo=True
    )

    # -----------------------------------------------------
    # VERIFICAR SE TERÁ ACESSO AO TPL
    # -----------------------------------------------------

    acesso_login = (
        request.form.get("acesso_login")
        == "sim"
    )

    if acesso_login:

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        senha = request.form.get(
            "senha",
            ""
        )

        tipo = request.form.get(
            "tipo",
            "IRMAO"
        ).strip().upper()

        # -------------------------------------------------
        # VALIDAÇÕES
        # -------------------------------------------------

        if not email:
            return (
                "Informe o e-mail para criar o acesso."
            ), 400

        if not senha:
            return (
                "Informe uma senha inicial para criar "
                "o acesso."
            ), 400

        if tipo not in (
            "IRMAO",
            "ADMIN"
        ):
            return "Tipo de usuário inválido.", 400

        usuario_existente = Usuario.query.filter_by(
            email=email
        ).first()

        if usuario_existente:
            return (
                "Já existe um usuário com este e-mail."
            ), 400

        # -------------------------------------------------
        # CRIAR USUÁRIO
        # -------------------------------------------------

        usuario = Usuario(
            nome=nome,
            email=email,
            senha=generate_password_hash(senha),
            tipo=tipo,
            ativo=True
        )

        db.session.add(usuario)

        # Gera o ID do usuário antes do commit
        db.session.flush()

        # Liga a pessoa ao usuário
        pessoa.usuario_id = usuario.id

    # -----------------------------------------------------
    # SALVAR PESSOA
    # -----------------------------------------------------

    db.session.add(pessoa)

    db.session.commit()

    return redirect(
        url_for("routes.admin_pessoas")
    )


@routes.route(
    "/admin/pessoas/editar/<int:pessoa_id>",
    methods=["POST"]
)
@login_required
def editar_pessoa(pessoa_id):

    if current_user.tipo != "ADMIN":
        return "Acesso permitido somente para administradores.", 403

    pessoa = Pessoa.query.get_or_404(
        pessoa_id
    )

    # -----------------------------------------------------
    # NOME
    # -----------------------------------------------------

    nome = request.form.get(
        "nome",
        ""
    ).strip()

    if not nome:
        return "Informe o nome do irmão.", 400

    outra_pessoa = (
        Pessoa.query
        .filter(
            Pessoa.nome == nome,
            Pessoa.id != pessoa.id
        )
        .first()
    )

    if outra_pessoa:
        return (
            "Já existe uma pessoa com esse nome."
        ), 400

    pessoa.nome = nome

    # -----------------------------------------------------
    # ACESSO AO TPL
    # -----------------------------------------------------

    acesso_login = (
        request.form.get("acesso_login")
        == "sim"
    )

    if acesso_login:

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        senha = request.form.get(
            "senha",
            ""
        )

        tipo = request.form.get(
            "tipo",
            "IRMAO"
        ).strip().upper()

        # -------------------------------------------------
        # VALIDAÇÕES
        # -------------------------------------------------

        if not email:
            return (
                "Informe o e-mail para o acesso."
            ), 400

        if tipo not in (
            "IRMAO",
            "ADMIN"
        ):
            return "Tipo de usuário inválido.", 400

        # -------------------------------------------------
        # PESSOA JÁ POSSUI USUÁRIO
        # -------------------------------------------------

        if pessoa.usuario:

            outro_usuario = (
                Usuario.query
                .filter(
                    Usuario.email == email,
                    Usuario.id != pessoa.usuario.id
                )
                .first()
            )

            if outro_usuario:
                return (
                    "Já existe outro usuário com este e-mail."
                ), 400

            pessoa.usuario.nome = nome
            pessoa.usuario.email = email
            pessoa.usuario.tipo = tipo
            pessoa.usuario.ativo = True

            # Só altera a senha se o administrador
            # informar uma nova senha.
            if senha:
                pessoa.usuario.senha = (
                    generate_password_hash(senha)
                )

        # -------------------------------------------------
        # PESSOA AINDA NÃO POSSUI USUÁRIO
        # -------------------------------------------------

        else:

            usuario_existente = (
                Usuario.query
                .filter_by(email=email)
                .first()
            )

            if usuario_existente:
                return (
                    "Já existe um usuário com este e-mail."
                ), 400

            if not senha:
                return (
                    "Informe uma senha inicial para criar "
                    "o acesso desta pessoa."
                ), 400

            usuario = Usuario(
                nome=nome,
                email=email,
                senha=generate_password_hash(senha),
                tipo=tipo,
                ativo=True
            )

            db.session.add(usuario)

            db.session.flush()

            pessoa.usuario_id = usuario.id

    # -----------------------------------------------------
    # ACESSO DESATIVADO
    # -----------------------------------------------------

    else:

        if pessoa.usuario:
            pessoa.usuario.ativo = False

    # -----------------------------------------------------
    # SALVAR
    # -----------------------------------------------------

    db.session.commit()

    return redirect(
        url_for("routes.admin_pessoas")
    )


@routes.route(
    "/admin/pessoas/alternar/<int:pessoa_id>",
    methods=["POST"]
)
@login_required
def alternar_pessoa(pessoa_id):

    if current_user.tipo != "ADMIN":
        return "Acesso permitido somente para administradores.", 403

    pessoa = Pessoa.query.get_or_404(
        pessoa_id
    )

    # Alterna ativo/inativo
    pessoa.ativo = not pessoa.ativo

    # Se a pessoa for desativada,
    # o login dela também fica inativo.
    if not pessoa.ativo:

        if pessoa.usuario:
            pessoa.usuario.ativo = False

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
@login_required
def excluir_pessoa(pessoa_id):

    if current_user.tipo != "ADMIN":
        return "Acesso permitido somente para administradores.", 403

    pessoa = Pessoa.query.get_or_404(pessoa_id)

    # Não permite que o administrador exclua a própria conta
    if pessoa.usuario_id == current_user.id:
        return "Você não pode excluir o próprio usuário administrador.", 400

    try:
        # Guarda o usuário relacionado, se existir
        usuario = pessoa.usuario

        # Exclui a pessoa
        db.session.delete(pessoa)

        # Se essa pessoa possuir um usuário de login,
        # exclui também o usuário relacionado.
        if usuario:
            db.session.delete(usuario)

        db.session.commit()

    except Exception:
        db.session.rollback()

        return (
            "Não foi possível excluir esta pessoa. "
            "Ela pode estar vinculada a algum registro existente."
        ), 400

    return redirect(
        url_for("routes.admin_pessoas")
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
        return (
            "Você não tem permissão para fazer CHECK-IN "
            "nesta reserva."
        ), 403

    if not reserva.ativo:
        return "Esta reserva não está ativa.", 400

    agora = datetime.now()
    hoje = agora.date()

    if reserva.tipo == "FIXA":

        if hoje < reserva.data_inicio:
            return (
                "O CHECK-IN ainda não está disponível."
            ), 400

        if reserva.dia_semana != hoje.weekday():
            return (
                "O CHECK-IN só está disponível no dia "
                "programado para esta reserva."
            ), 400

        data_ocorrencia = hoje

    else:

        if hoje < reserva.data_inicio:
            return (
                "O CHECK-IN ainda não está disponível. "
                f"Seu horário começa em "
                f"{reserva.data_inicio.strftime('%d/%m/%Y')} "
                f"às {reserva.hora_inicio.strftime('%H:%M')}."
            ), 400

        if hoje > reserva.data_inicio:
            return "O horário desta reserva já passou.", 400

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
        return (
            "O CHECK-IN ainda não está disponível. "
            f"Seu horário começa às "
            f"{reserva.hora_inicio.strftime('%H:%M')}."
        ), 400

    if agora >= fim:
        return (
            "O horário desta reserva já terminou. "
            "O CHECK-IN não está mais disponível."
        ), 400

    uso_existente = UsoCarrinho.query.filter_by(
        reserva_id=reserva.id,
        checkout_em=None
    ).first()

    if uso_existente:
        return (
            "Esta reserva já está com o carrinho em uso."
        ), 400

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
        return (
            "Este carrinho está em uso no momento. "
            "Faça o CHECK-OUT quando devolvê-lo."
        ), 400

    uso = UsoCarrinho(
        reserva_id=reserva.id,
        checkin_em=agora
    )

    db.session.add(uso)
    db.session.commit()

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
        return (
            "Você não tem permissão para fazer CHECK-OUT "
            "desta reserva."
        ), 403

    uso = (
        UsoCarrinho.query
        .filter_by(
            reserva_id=reserva.id,
            checkout_em=None
        )
        .first()
    )

    if not uso:
        return (
            "Não existe um CHECK-IN ativo para esta reserva."
        ), 400

    uso.checkout_em = datetime.now()

    # Reserva única termina após a devolução.
    # Reserva fixa continua ativa para a próxima semana.
    if reserva.tipo != "FIXA":
        reserva.ativo = False

    db.session.commit()

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