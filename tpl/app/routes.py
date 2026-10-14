import os
import uuid
from datetime import date, datetime, timedelta
from functools import wraps
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    current_app,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify
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

from werkzeug.utils import secure_filename

from app.models import (
    db,
    Usuario,
    Programacao,
    ProgramacaoSemanal,
    Inscricao,
    Reserva,
    ReservaParticipante,
    UsoCarrinho,
    Carrinho,
    Local,
    Pessoa,
    Notificacao
)


routes = Blueprint("routes", __name__)

SAO_PAULO = ZoneInfo("America/Sao_Paulo")

EXTENSOES_FOTO = {"png", "jpg", "jpeg", "webp"}


def agora_sao_paulo():
    return datetime.now(SAO_PAULO)


def hoje_sao_paulo():
    return agora_sao_paulo().date()


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
# PERMISSÕES
# =========================================================

def admin_required(view):
    """Só deixa entrar usuário logado e do tipo ADMIN."""

    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):

        if current_user.tipo != "ADMIN":
            flash("Esta área é exclusiva para administradores.", "danger")
            return redirect(url_for("routes.agenda"))

        return view(*args, **kwargs)

    return wrapper


# =========================================================
# FUNÇÕES AUXILIARES
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


def voltar_admin_com_flash(endpoint, mensagem, categoria="warning"):
    flash(mensagem, categoria)
    return redirect(url_for(endpoint))


def pessoas_da_reserva(reserva):

    nomes = [reserva.nome_principal]

    for participante in reserva.participantes:
        nomes.append(participante.nome)

    return nomes


def reservas_no_horario(data, hora_inicio, hora_fim, carrinho_id=None):
    """
    Reservas ativas que ocupam um horário em uma data.

    Considera:
      - reservas únicas daquele dia;
      - reservas fixas (semanais) que já começaram.
    """

    filtros = [
        Reserva.ativo == True,
        Reserva.hora_inicio < hora_fim,
        Reserva.hora_fim > hora_inicio,
        db.or_(
            Reserva.data_inicio == data,
            db.and_(
                Reserva.tipo == "FIXA",
                Reserva.dia_semana == data.weekday(),
                Reserva.data_inicio <= data
            )
        )
    ]

    if carrinho_id is not None:
        filtros.append(Reserva.carrinho_id == carrinho_id)

    return Reserva.query.filter(*filtros).all()


def contar_pessoas(reservas):
    return sum(len(pessoas_da_reserva(r)) for r in reservas)


def garantir_programacao_do_dia(data):
    """
    Mantém a tabela Programacao de uma data igual à regra semanal.

    - Cria os horários que ainda não existem.
    - Para hoje e datas futuras, corrige o local e a capacidade
      se a regra semanal mudou.
    - Para hoje e datas futuras, remove horários antigos que não
      existem mais na regra semanal (somente se não houver reservas).

    Datas passadas nunca são alteradas, para preservar o histórico.
    """

    hoje = hoje_sao_paulo()

    semanais = (
        ProgramacaoSemanal.query
        .filter_by(dia_semana=data.weekday())
        .order_by(
            ProgramacaoSemanal.carrinho_id,
            ProgramacaoSemanal.hora_inicio
        )
        .all()
    )

    existentes = Programacao.query.filter_by(data=data).all()

    por_chave = {
        (p.carrinho_id, p.hora_inicio, p.hora_fim): p
        for p in existentes
    }

    chaves_validas = set()
    alterou = False

    for semanal in semanais:

        chave = (
            semanal.carrinho_id,
            semanal.hora_inicio,
            semanal.hora_fim
        )

        chaves_validas.add(chave)

        atual = por_chave.get(chave)

        if atual is None:

            db.session.add(Programacao(
                data=data,
                hora_inicio=semanal.hora_inicio,
                hora_fim=semanal.hora_fim,
                carrinho_id=semanal.carrinho_id,
                local_id=semanal.local_id,
                quantidade_maxima=semanal.quantidade_maxima
            ))

            alterou = True

        elif data >= hoje and (
            atual.local_id != semanal.local_id
            or atual.quantidade_maxima != semanal.quantidade_maxima
        ):

            atual.local_id = semanal.local_id
            atual.quantidade_maxima = semanal.quantidade_maxima

            alterou = True

    if data >= hoje:

        for chave, programacao in por_chave.items():

            if chave in chaves_validas:
                continue

            tem_reserva = reservas_no_horario(
                programacao.data,
                programacao.hora_inicio,
                programacao.hora_fim,
                programacao.carrinho_id
            )

            if tem_reserva:
                continue

            tem_inscricao = Inscricao.query.filter_by(
                programacao_id=programacao.id
            ).first()

            if tem_inscricao:
                continue

            db.session.delete(programacao)
            alterou = True

    if alterou:
        db.session.commit()


# =========================================================
# BUSCA RÁPIDA
# =========================================================

@routes.route("/buscar")
@login_required
def buscar():
    termo = request.args.get("q", "").strip()

    if not termo:
        return {"resultados": []}

    busca = f"%{termo}%"

    resultados = []

    # Pessoas → abrem a página de perfil
    pessoas = (
        Pessoa.query
        .filter(
            Pessoa.nome.ilike(busca),
            Pessoa.ativo == True
        )
        .order_by(Pessoa.nome)
        .limit(10)
        .all()
    )

    for pessoa in pessoas:
        resultados.append({
            "tipo": "pessoa",
            "icone": "👤",
            "foto": (
                url_for("static", filename=pessoa.foto)
                if pessoa.foto else None
            ),
            "titulo": pessoa.nome,
            "subtitulo": "Irmão",
            "url": url_for("routes.perfil", pessoa_id=pessoa.id)
        })

    # Carrinhos
    carrinhos = (
        Carrinho.query
        .filter(
            Carrinho.nome.ilike(busca),
            Carrinho.ativo == True
        )
        .order_by(Carrinho.nome)
        .limit(10)
        .all()
    )

    for carrinho in carrinhos:
        resultados.append({
            "tipo": "carrinho",
            "icone": "🛒",
            "titulo": carrinho.nome,
            "subtitulo": "Carrinho",
            "url": url_for("routes.agenda")
        })

    # Locais
    locais = (
        Local.query
        .filter(
            Local.nome.ilike(busca),
            Local.ativo == True
        )
        .order_by(Local.nome)
        .limit(10)
        .all()
    )

    for local in locais:
        resultados.append({
            "tipo": "local",
            "icone": "📍",
            "titulo": local.nome,
            "subtitulo": "Local",
            "url": url_for("routes.agenda", local=local.nome)
        })

    # Reservas
    reservas = (
        Reserva.query
        .filter(
            Reserva.ativo == True,
            Reserva.nome_principal.ilike(busca)
        )
        .order_by(Reserva.data_inicio, Reserva.hora_inicio)
        .limit(10)
        .all()
    )

    for reserva in reservas:
        resultados.append({
            "tipo": "reserva",
            "icone": "📋",
            "titulo": reserva.nome_principal,
            "subtitulo": (
                f"{reserva.data_inicio.strftime('%d/%m/%Y')} "
                f"• {reserva.hora_inicio.strftime('%H:%M')} "
                f"às {reserva.hora_fim.strftime('%H:%M')} "
                f"• {reserva.carrinho.nome}"
            ),
            "url": url_for(
                "routes.agenda",
                data=reserva.data_inicio.isoformat()
            )
        })

    return {"resultados": resultados}


# =========================================================
# DADOS DO CALENDÁRIO
# =========================================================

def dados_do_calendario():

    reservas = Reserva.query.filter(
        Reserva.ativo == True
    ).all()

    return [
        {
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
        }
        for reserva in reservas
    ]


# =========================================================
# AGENDA
# =========================================================

@routes.route("/")
@login_required
def agenda():

    hoje = hoje_sao_paulo()

    data_parametro = request.args.get("data")

    try:
        data_selecionada = (
            date.fromisoformat(data_parametro)
            if data_parametro
            else hoje
        )
    except ValueError:
        data_selecionada = hoje

    # Garante que os horários desta data existam e estejam atualizados
    garantir_programacao_do_dia(data_selecionada)

    # Segunda-feira da semana
    segunda = data_selecionada - timedelta(
        days=data_selecionada.weekday()
    )

    nomes_dias = ["SEG", "TER", "QUA", "QUI", "SEX", "SÁB", "DOM"]

    dias_semana = [
        {
            "data": segunda + timedelta(days=i),
            "nome": nomes_dias[i]
        }
        for i in range(7)
    ]

    # -----------------------------------------------------
    # PROGRAMAÇÕES DO DIA
    # -----------------------------------------------------

    programacoes = (
        Programacao.query
        .filter_by(data=data_selecionada)
        .order_by(
            Programacao.carrinho_id,
            Programacao.hora_inicio
        )
        .all()
    )

    # -----------------------------------------------------
    # AGRUPAR POR CARRINHO
    # -----------------------------------------------------

    carrinhos = {}

    for programacao in programacoes:

        numero = programacao.carrinho_id

        if numero not in carrinhos:
            carrinhos[numero] = {
                "carrinho": programacao.carrinho,
                "local": programacao.local,
                "horarios": []
            }

        reservas = reservas_no_horario(
            programacao.data,
            programacao.hora_inicio,
            programacao.hora_fim,
            programacao.carrinho_id
        )

        nomes = []

        for reserva in reservas:
            nomes.extend(pessoas_da_reserva(reserva))

        total = len(nomes)

        carrinhos[numero]["horarios"].append({
            "programacao": programacao,
            "nomes": nomes,
            "total": total,
            "vagas": max(programacao.quantidade_maxima - total, 0),
            "reservas": reservas,
            # o agenda.html usa horario.reserva
            "reserva": reservas[0] if reservas else None
        })

    pessoa_menu = Pessoa.query.filter_by(
        usuario_id=current_user.id
    ).first()

    return render_template(
        "agenda.html",
        carrinhos=list(carrinhos.values()),
        dias_semana=dias_semana,
        data_selecionada=data_selecionada,
        hoje=hoje,
        segunda=segunda,
        timedelta=timedelta,
        reservas_calendario=dados_do_calendario(),
        pessoa_menu=pessoa_menu
    )



# =========================================================
# LOGIN / LOGOUT
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

            # Só aceita caminhos internos (evita redirecionar para outro site)
            if proxima and proxima.startswith("/") and not proxima.startswith("//"):
                return redirect(proxima)

            return redirect(url_for("routes.agenda"))

        flash("E-mail ou senha incorretos.")

    return render_template("login.html")


@routes.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("routes.login"))


# =========================================================
# PERFIL
# =========================================================

def pode_editar_perfil(pessoa):
    """Só a própria pessoa (login vinculado) ou um ADMIN."""
    return (
        current_user.tipo == "ADMIN"
        or pessoa.usuario_id == current_user.id
    )


@routes.route("/meu-perfil")
@login_required
def meu_perfil():

    pessoa = Pessoa.query.filter_by(usuario_id=current_user.id).first()

    if not pessoa:
        flash("Seu usuário não está vinculado a nenhuma pessoa.", "warning")
        return redirect(url_for("routes.agenda"))

    return redirect(url_for("routes.perfil", pessoa_id=pessoa.id))


@routes.route("/perfil/<int:pessoa_id>")
@login_required
def perfil(pessoa_id):

    pessoa = Pessoa.query.get_or_404(pessoa_id)
    hoje = hoje_sao_paulo()

    # Reservas em que a pessoa é a principal ou participante
    reservas = (
        Reserva.query
        .filter(
            Reserva.ativo == True,
            db.or_(
                Reserva.nome_principal == pessoa.nome,
                Reserva.id.in_(
                    db.session.query(ReservaParticipante.reserva_id)
                    .filter(ReservaParticipante.nome == pessoa.nome)
                )
            )
        )
        .order_by(Reserva.data_inicio, Reserva.hora_inicio)
        .all()
    )

    fixas = [r for r in reservas if r.tipo == "FIXA"]
    proximas = [r for r in reservas if r.tipo != "FIXA" and r.data_inicio >= hoje]
    passadas = [r for r in reservas if r.tipo != "FIXA" and r.data_inicio < hoje]
    passadas.reverse()

    return render_template(
        "perfil.html",
        pessoa=pessoa,
        fixas=fixas,
        proximas=proximas,
        passadas=passadas[:10],
        pode_editar=pode_editar_perfil(pessoa),
        dias=DIAS_SEMANA
    )


@routes.route("/perfil/<int:pessoa_id>/foto", methods=["POST"])
@login_required
def alterar_foto(pessoa_id):

    pessoa = Pessoa.query.get_or_404(pessoa_id)

    if not pode_editar_perfil(pessoa):
        flash("Você não pode alterar a foto deste perfil.", "danger")
        return redirect(url_for("routes.perfil", pessoa_id=pessoa.id))

    arquivo = request.files.get("foto")

    if not arquivo or not arquivo.filename:
        flash("Escolha uma imagem.", "warning")
        return redirect(url_for("routes.perfil", pessoa_id=pessoa.id))

    extensao = secure_filename(arquivo.filename).rsplit(".", 1)[-1].lower()

    if extensao not in EXTENSOES_FOTO:
        flash("Formato inválido. Use PNG, JPG ou WEBP.", "warning")
        return redirect(url_for("routes.perfil", pessoa_id=pessoa.id))

    pasta = os.path.join(current_app.static_folder, "uploads", "fotos")
    os.makedirs(pasta, exist_ok=True)

    nome_arquivo = f"{pessoa.id}_{uuid.uuid4().hex}.{extensao}"
    arquivo.save(os.path.join(pasta, nome_arquivo))

    # Apaga a foto antiga
    if pessoa.foto:
        antiga = os.path.join(current_app.static_folder, pessoa.foto)
        if os.path.exists(antiga):
            os.remove(antiga)

    pessoa.foto = f"uploads/fotos/{nome_arquivo}"
    db.session.commit()

    flash("Foto atualizada!", "success")
    return redirect(url_for("routes.perfil", pessoa_id=pessoa.id))


# =========================================================
# NOTIFICAÇÕES
# =========================================================

@routes.route("/notificacoes")
@login_required
def notificacoes():

    lista = (
        Notificacao.query
        .filter_by(usuario_id=current_user.id)
        .order_by(Notificacao.criado_em.desc())
        .all()
    )

    quantidade_nao_lidas = Notificacao.query.filter_by(
        usuario_id=current_user.id,
        lida=False
    ).count()

    return jsonify({
        "quantidade": quantidade_nao_lidas,
        "notificacoes": [
            {
                "id": notificacao.id,
                "titulo": notificacao.titulo,
                "mensagem": notificacao.mensagem,
                "lida": notificacao.lida,
                "criado_em": (
                    notificacao.criado_em.strftime("%d/%m/%Y %H:%M")
                    if notificacao.criado_em
                    else ""
                )
            }
            for notificacao in lista
        ]
    })


@routes.route("/notificacoes/<int:notificacao_id>", methods=["DELETE"])
@login_required
def excluir_notificacao(notificacao_id):

    notificacao = Notificacao.query.filter_by(
        id=notificacao_id,
        usuario_id=current_user.id
    ).first()

    if not notificacao:
        return jsonify({
            "sucesso": False,
            "mensagem": "Notificação não encontrada."
        }), 404

    db.session.delete(notificacao)
    db.session.commit()

    return jsonify({"sucesso": True})


# =========================================================
# RESERVAR HORÁRIO
# =========================================================

@routes.route(
    "/participar/<int:programacao_id>",
    methods=["GET", "POST"]
)
@login_required
def participar(programacao_id):

    programacao = Programacao.query.get_or_404(
        programacao_id
    )

    hoje = hoje_sao_paulo()

    # Impede reserva para data que já passou
    if programacao.data < hoje:
        return voltar_agenda_com_flash(
            hoje,
            "Não é possível fazer uma reserva para uma data que já passou.",
            "warning"
        )

    pessoas = (
        Pessoa.query
        .filter_by(ativo=True)
        .order_by(Pessoa.nome)
        .all()
    )

    if request.method == "POST":

        nome_principal = request.form.get(
            "nome_principal", ""
        ).strip()

        selecionados = request.form.getlist("pessoas")

        reserva_fixa = request.form.get("reserva_fixa") == "sim"

        if not nome_principal:
            return voltar_agenda_com_flash(
                programacao.data,
                "Informe seu nome para continuar.",
                "warning"
            )

        if len(selecionados) > 2:
            return voltar_agenda_com_flash(
                programacao.data,
                "Você pode escolher no máximo 2 irmãos além de você.",
                "warning"
            )

        nomes_nova_reserva = [nome_principal] + selecionados

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

        # Todas as reservas do mesmo horário (todos os carrinhos)
        reservas = reservas_no_horario(
            programacao.data,
            programacao.hora_inicio,
            programacao.hora_fim
        )

        pessoas_ocupadas = set()

        for reserva in reservas:
            for nome in pessoas_da_reserva(reserva):
                pessoas_ocupadas.add(nome.strip().lower())

        for nome in nomes_nova_reserva:

            if nome.strip().lower() in pessoas_ocupadas:
                return voltar_agenda_com_flash(
                    programacao.data,
                    f"{nome} já está participando de outro carrinho neste horário.",
                    "warning"
                )

        # Capacidade deste carrinho
        reservas_carrinho = [
            r for r in reservas
            if r.carrinho_id == programacao.carrinho_id
        ]

        pessoas_no_carrinho = contar_pessoas(reservas_carrinho)

        if (
            pessoas_no_carrinho + len(nomes_nova_reserva)
            > programacao.quantidade_maxima
        ):
            return voltar_agenda_com_flash(
                programacao.data,
                "Este carrinho não possui vagas suficientes.",
                "danger"
            )

        # Criar reserva
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

        # Participantes e convites
        for nome in selecionados:

            db.session.add(ReservaParticipante(
                reserva_id=reserva.id,
                nome=nome
            ))

            pessoa = Pessoa.query.filter_by(nome=nome).first()

            if pessoa and pessoa.usuario_id:
                db.session.add(Notificacao(
                    usuario_id=pessoa.usuario_id,
                    reserva_id=reserva.id,
                    titulo="Você recebeu um convite!",
                    mensagem=(
                        f"Oii! Você recebeu um convite de "
                        f"{current_user.nome} para trabalharem juntos!"
                    ),
                    lida=False
                ))

        db.session.commit()

        return voltar_agenda_com_flash(
            programacao.data,
            "Reserva feita com sucesso!",
            "success"
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
        return redirect(
            url_for("routes.agenda", data=hoje_sao_paulo().isoformat())
        )

    data = reserva.data_inicio

    # Não apagamos fisicamente. Apenas desativamos a reserva.
    reserva.ativo = False

    db.session.commit()

    flash("Reserva excluída com sucesso.", "success")

    return redirect(
        url_for("routes.agenda", data=data.isoformat())
    )


# =========================================================
# CANCELAR RESERVA FIXA
# =========================================================

@routes.route(
    "/cancelar-reserva-fixa/<int:reserva_id>",
    methods=["POST"]
)
@login_required
def cancelar_reserva_fixa(reserva_id):

    reserva = Reserva.query.get_or_404(reserva_id)

    if current_user.tipo != "ADMIN" and reserva.usuario_id != current_user.id:
        return voltar_agenda_com_flash(
            hoje_sao_paulo(),
            "Você não tem permissão para cancelar esta reserva.",
            "danger"
        )

    if reserva.tipo != "FIXA":
        return voltar_agenda_com_flash(
            hoje_sao_paulo(),
            "Esta não é uma reserva fixa.",
            "danger"
        )

    reserva.ativo = False

    db.session.commit()

    return redirect(
        url_for("routes.agenda", data=hoje_sao_paulo().isoformat())
    )


# =========================================================
# NOVA RESERVA
# =========================================================

@routes.route("/nova-reserva")
@login_required
def nova_reserva():

    carrinhos = Carrinho.query.filter_by(ativo=True).all()

    locais = Local.query.filter_by(ativo=True).all()

    programacao_id = request.args.get("programacao_id")

    programacao = None

    if programacao_id and programacao_id.isdigit():
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
@admin_required
def admin():

    return render_template("admin.html")


# =========================================================
# ADMIN - CARRINHOS
# =========================================================

@routes.route("/admin/carrinhos")
@admin_required
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
@admin_required
def adicionar_carrinho():

    nome = request.form.get("nome", "").strip()

    if not nome:
        return "Informe o nome do carrinho.", 400

    if Carrinho.query.filter_by(nome=nome).first():
        return "Já existe um carrinho com esse nome.", 400

    db.session.add(Carrinho(nome=nome, ativo=True))
    db.session.commit()

    return redirect(url_for("routes.admin_carrinhos"))


@routes.route(
    "/admin/carrinhos/editar/<int:carrinho_id>",
    methods=["POST"]
)
@admin_required
def editar_carrinho(carrinho_id):

    carrinho = Carrinho.query.get_or_404(carrinho_id)

    nome = request.form.get("nome", "").strip()

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

    return redirect(url_for("routes.admin_carrinhos"))


@routes.route(
    "/admin/carrinhos/alternar/<int:carrinho_id>",
    methods=["POST"]
)
@admin_required
def alternar_carrinho(carrinho_id):

    carrinho = Carrinho.query.get_or_404(carrinho_id)

    carrinho.ativo = not carrinho.ativo

    db.session.commit()

    return redirect(url_for("routes.admin_carrinhos"))


# =========================================================
# ADMIN - PESSOAS
# =========================================================

@routes.route("/admin/pessoas")
@admin_required
def admin_pessoas():

    pessoas = Pessoa.query.order_by(Pessoa.nome).all()

    return render_template(
        "admin_pessoas.html",
        pessoas=pessoas
    )


def _marcado(valor):
    return valor.strip().lower() in ["sim", "on", "true", "1", "yes"]


@routes.route(
    "/admin/pessoas/adicionar",
    methods=["POST"]
)
@admin_required
def adicionar_pessoa():

    nome = request.form.get("nome", "").strip()

    permitir_acesso = _marcado(
        request.form.get("permitir_acesso", "")
    )

    email = request.form.get("email", "").strip().lower()

    senha = request.form.get("senha", "")

    if not nome:
        return "Informe o nome do irmão.", 400

    if Pessoa.query.filter_by(nome=nome).first():
        return "Esta pessoa já está cadastrada.", 400

    if permitir_acesso:

        if not email:
            return "Informe o e-mail para criar o acesso ao TPL.", 400

        if not senha:
            return "Informe uma senha inicial para criar o acesso.", 400

        if Usuario.query.filter_by(email=email).first():
            return "Este e-mail já está cadastrado.", 400

    pessoa = Pessoa(nome=nome, ativo=True)

    db.session.add(pessoa)

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

    return redirect(url_for("routes.admin_pessoas"))


@routes.route(
    "/admin/pessoas/editar/<int:pessoa_id>",
    methods=["POST"]
)
@admin_required
def editar_pessoa(pessoa_id):

    pessoa = Pessoa.query.get_or_404(pessoa_id)

    nome = request.form.get("nome", "").strip()

    permitir_acesso = _marcado(
        request.form.get("permitir_acesso", "")
    )

    email = request.form.get("email", "").strip().lower()

    senha = request.form.get("senha", "").strip()

    tipo = request.form.get("tipo", "IRMAO").strip().upper()

    if not nome:
        return voltar_admin_com_flash(
            "routes.admin_pessoas",
            "Informe o nome do irmão."
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
        return voltar_admin_com_flash(
            "routes.admin_pessoas",
            "Já existe uma pessoa com esse nome."
        )

    pessoa.nome = nome

    if permitir_acesso:

        if not email:
            return voltar_admin_com_flash(
                "routes.admin_pessoas",
                "Informe o e-mail para permitir o acesso ao TPL."
            )

        if tipo not in ["IRMAO", "ADMIN"]:
            tipo = "IRMAO"

        usuario_atual_id = (
            pessoa.usuario.id if pessoa.usuario else None
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
            return voltar_admin_com_flash(
                "routes.admin_pessoas",
                "Este e-mail já está sendo usado por outro usuário."
            )

        if pessoa.usuario:

            usuario = pessoa.usuario

            usuario.nome = nome
            usuario.email = email
            usuario.tipo = tipo
            usuario.ativo = True

            # Só troca a senha se uma nova senha foi informada
            if senha:
                usuario.senha = generate_password_hash(senha)

        else:

            if not senha:
                return voltar_admin_com_flash(
                    "routes.admin_pessoas",
                    "Informe uma senha para criar o acesso ao TPL."
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

            pessoa.usuario_id = usuario.id

    else:

        if pessoa.usuario:
            # Mantém o usuário no banco, mas impede o login
            pessoa.usuario.ativo = False

    db.session.commit()

    return voltar_admin_com_flash(
        "routes.admin_pessoas",
        f"{pessoa.nome} foi atualizado com sucesso.",
        "success"
    )


@routes.route(
    "/admin/pessoas/alternar/<int:pessoa_id>",
    methods=["POST"]
)
@admin_required
def alternar_pessoa(pessoa_id):

    pessoa = Pessoa.query.get_or_404(pessoa_id)

    pessoa.ativo = not pessoa.ativo

    db.session.commit()

    return redirect(url_for("routes.admin_pessoas"))


@routes.route(
    "/admin/pessoas/excluir/<int:pessoa_id>",
    methods=["POST"]
)
@admin_required
def excluir_pessoa(pessoa_id):

    pessoa = Pessoa.query.get_or_404(pessoa_id)

    nome = pessoa.nome

    usuario = pessoa.usuario

    # O usuário já possui reservas?
    possui_reservas = False

    if usuario:
        possui_reservas = (
            Reserva.query
            .filter_by(usuario_id=usuario.id)
            .first()
            is not None
        )

    # Remove o vínculo antes de apagar a pessoa.
    if usuario:
        pessoa.usuario_id = None
        db.session.flush()

    db.session.delete(pessoa)
    db.session.flush()

    if usuario:

        if possui_reservas:
            # As reservas históricas continuam apontando para o
            # usuário, então ele fica no banco, sem acesso.
            usuario.ativo = False
        else:
            db.session.delete(usuario)

    try:
        db.session.commit()

    except Exception:

        db.session.rollback()

        return voltar_admin_com_flash(
            "routes.admin_pessoas",
            "Não foi possível excluir esta pessoa. "
            "Verifique se ela possui registros vinculados.",
            "danger"
        )

    return voltar_admin_com_flash(
        "routes.admin_pessoas",
        f"{nome} foi excluído com sucesso.",
        "success"
    )


# =========================================================
# ADMIN - LOCAIS
# =========================================================

@routes.route("/admin/locais")
@admin_required
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
@admin_required
def adicionar_local():

    nome = request.form.get("nome", "").strip()

    # Permite adicionar um local a partir da tela de Programação
    # e voltar para ela.
    destino = (
        "routes.admin_programacao"
        if request.form.get("next") == "programacao"
        else "routes.admin_locais"
    )

    if not nome:
        return voltar_admin_com_flash(
            destino,
            "Informe o nome do local."
        )

    existente = (
        Local.query
        .filter(db.func.lower(Local.nome) == nome.lower())
        .first()
    )

    if existente:
        return voltar_admin_com_flash(
            destino,
            "Já existe um local com esse nome."
        )

    db.session.add(Local(nome=nome, ativo=True))
    db.session.commit()

    return voltar_admin_com_flash(
        destino,
        f"Local “{nome}” adicionado.",
        "success"
    )


@routes.route(
    "/admin/locais/editar/<int:local_id>",
    methods=["POST"]
)
@admin_required
def editar_local(local_id):

    local = Local.query.get_or_404(local_id)

    nome = request.form.get("nome", "").strip()

    if not nome:
        return voltar_admin_com_flash(
            "routes.admin_locais",
            "Informe o nome do local."
        )

    outro_local = (
        Local.query
        .filter(
            db.func.lower(Local.nome) == nome.lower(),
            Local.id != local.id
        )
        .first()
    )

    if outro_local:
        return voltar_admin_com_flash(
            "routes.admin_locais",
            "Já existe um local com esse nome."
        )

    local.nome = nome

    db.session.commit()

    return voltar_admin_com_flash(
        "routes.admin_locais",
        "Local atualizado.",
        "success"
    )


@routes.route(
    "/admin/locais/alternar/<int:local_id>",
    methods=["POST"]
)
@admin_required
def alternar_local(local_id):

    local = Local.query.get_or_404(local_id)

    local.ativo = not local.ativo

    db.session.commit()

    return voltar_admin_com_flash(
        "routes.admin_locais",
        f"“{local.nome}” agora está "
        f"{'ativo' if local.ativo else 'inativo'}.",
        "success"
    )


@routes.route(
    "/admin/locais/excluir/<int:local_id>",
    methods=["POST"]
)
@admin_required
def excluir_local(local_id):

    local = Local.query.get_or_404(local_id)

    em_uso = (
        ProgramacaoSemanal.query.filter_by(local_id=local.id).first()
        or Programacao.query.filter_by(local_id=local.id).first()
        or Reserva.query.filter_by(local_id=local.id).first()
    )

    if em_uso:
        return voltar_admin_com_flash(
            "routes.admin_locais",
            "Este local já foi usado em uma programação ou reserva e "
            "não pode ser excluído. Use “Desativar” para escondê-lo.",
            "warning"
        )

    db.session.delete(local)
    db.session.commit()

    return voltar_admin_com_flash(
        "routes.admin_locais",
        "Local excluído.",
        "success"
    )


# =========================================================
# CHECK-IN DO CARRINHO
# =========================================================

@routes.route("/checkin/<int:reserva_id>", methods=["POST"])
@login_required
def checkin(reserva_id):

    reserva = Reserva.query.get_or_404(reserva_id)

    # O responsável pela reserva pode fazer CHECK-IN. ADMIN também.
    if (
        current_user.tipo != "ADMIN"
        and reserva.usuario_id != current_user.id
    ):
        flash("Você não tem permissão para fazer o check-in desta reserva.", "danger")
        return redirect(url_for("routes.agenda", data=hoje_sao_paulo().isoformat()))

    if not reserva.ativo:
        flash("Esta reserva não está mais ativa.", "danger")
        return redirect(url_for("routes.agenda", data=hoje_sao_paulo().isoformat()))

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
                f"O check-in ainda não está disponível. Seu horário começa em "
                f"{reserva.data_inicio.strftime('%d/%m/%Y')} às "
                f"{reserva.hora_inicio.strftime('%H:%M')}.",
                "warning"
            )
            return redirect(url_for("routes.agenda", data=reserva.data_inicio.isoformat()))

        if hoje > reserva.data_inicio:
            flash("O horário desta reserva já passou.", "warning")
            return redirect(url_for("routes.agenda", data=hoje.isoformat()))

        data_ocorrencia = reserva.data_inicio

    # IMPORTANTE: "agora" tem fuso horário, então início e fim também
    # precisam ter. Sem isso o Python dá erro ao comparar os dois.
    inicio = datetime.combine(
        data_ocorrencia, reserva.hora_inicio, tzinfo=SAO_PAULO
    )

    fim = datetime.combine(
        data_ocorrencia, reserva.hora_fim, tzinfo=SAO_PAULO
    )

    if agora < inicio:
        flash(
            f"O check-in ainda não está disponível. Seu horário começa às "
            f"{reserva.hora_inicio.strftime('%H:%M')}.",
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

    db.session.add(UsoCarrinho(
        reserva_id=reserva.id,
        checkin_em=agora
    ))

    db.session.commit()

    flash("Sua presença foi confirmada. Check-in realizado com sucesso!", "success")

    return redirect(
        url_for("routes.agenda", data=data_ocorrencia.isoformat())
    )


# =========================================================
# CHECK-OUT DO CARRINHO
# =========================================================

@routes.route("/checkout/<int:reserva_id>", methods=["POST"])
@login_required
def checkout(reserva_id):

    reserva = Reserva.query.get_or_404(reserva_id)

    if (
        current_user.tipo != "ADMIN"
        and reserva.usuario_id != current_user.id
    ):
        flash("Você não tem permissão para fazer o check-out desta reserva.", "danger")
        return redirect(url_for("routes.agenda", data=hoje_sao_paulo().isoformat()))

    uso = (
        UsoCarrinho.query
        .filter_by(reserva_id=reserva.id, checkout_em=None)
        .first()
    )

    if not uso:
        flash("Não existe um check-in ativo para esta reserva.", "warning")
        return redirect(url_for("routes.agenda", data=hoje_sao_paulo().isoformat()))

    uso.checkout_em = agora_sao_paulo()

    # Reserva única termina após a devolução.
    # Reserva fixa continua ativa para a próxima semana.
    if reserva.tipo != "FIXA":
        reserva.ativo = False

    db.session.commit()

    flash("Check-out realizado. Obrigado por devolver o carrinho!", "success")

    data_retorno = (
        hoje_sao_paulo()
        if reserva.tipo == "FIXA"
        else reserva.data_inicio
    )

    return redirect(
        url_for("routes.agenda", data=data_retorno.isoformat())
    )


# =========================================================
# ADMIN - PROGRAMAÇÃO SEMANAL
# =========================================================

DIAS_SEMANA = {
    0: "Segunda-feira",
    1: "Terça-feira",
    2: "Quarta-feira",
    3: "Quinta-feira",
    4: "Sexta-feira",
    5: "Sábado",
    6: "Domingo"
}


@routes.route("/admin/programacao")
@admin_required
def admin_programacao():

    programacoes = (
        ProgramacaoSemanal.query
        .order_by(
            ProgramacaoSemanal.dia_semana,
            ProgramacaoSemanal.hora_inicio,
            ProgramacaoSemanal.carrinho_id
        )
        .all()
    )

    programacao_por_dia = {}

    for programacao in programacoes:
        programacao_por_dia.setdefault(
            programacao.dia_semana, []
        ).append(programacao)

    return render_template(
        "admin_programacao.html",
        programacao_por_dia=programacao_por_dia,
        dias=DIAS_SEMANA,
        carrinhos=(
            Carrinho.query
            .filter_by(ativo=True)
            .order_by(Carrinho.id)
            .all()
        ),
        locais=(
            Local.query
            .filter_by(ativo=True)
            .order_by(Local.nome)
            .all()
        )
    )


def propagar_mudanca_programacao(semanal, antigo):
    """
    Leva a alteração da regra semanal para o que já foi gerado:

      1. Programacao (horários de datas) de hoje em diante;
      2. Reservas ativas que estavam nesse horário
         (fixas, e únicas de hoje em diante).

    Datas e reservas únicas do passado ficam como estavam (histórico).
    """

    hoje = hoje_sao_paulo()
    dia = semanal.dia_semana

    carrinho_antigo, hora_inicio_antiga, hora_fim_antiga = antigo

    # 1. Horários já gerados na agenda
    geradas = (
        Programacao.query
        .filter(
            Programacao.data >= hoje,
            Programacao.carrinho_id == carrinho_antigo,
            Programacao.hora_inicio == hora_inicio_antiga,
            Programacao.hora_fim == hora_fim_antiga
        )
        .all()
    )

    for programacao in geradas:

        if programacao.data.weekday() != dia:
            continue

        # Se já existir um horário igual ao novo nessa data,
        # deixa o antigo; a agenda limpa sozinha depois.
        duplicado = (
            Programacao.query
            .filter(
                Programacao.id != programacao.id,
                Programacao.data == programacao.data,
                Programacao.carrinho_id == semanal.carrinho_id,
                Programacao.hora_inicio == semanal.hora_inicio,
                Programacao.hora_fim == semanal.hora_fim
            )
            .first()
        )

        if duplicado:
            continue

        programacao.carrinho_id = semanal.carrinho_id
        programacao.local_id = semanal.local_id
        programacao.hora_inicio = semanal.hora_inicio
        programacao.hora_fim = semanal.hora_fim
        programacao.quantidade_maxima = semanal.quantidade_maxima

    # 2. Reservas que estavam nesse horário
    reservas = (
        Reserva.query
        .filter(
            Reserva.ativo == True,
            Reserva.carrinho_id == carrinho_antigo,
            Reserva.hora_inicio == hora_inicio_antiga,
            Reserva.hora_fim == hora_fim_antiga
        )
        .all()
    )

    for reserva in reservas:

        if reserva.tipo == "FIXA":
            afetada = reserva.dia_semana == dia
        else:
            afetada = (
                reserva.data_inicio >= hoje
                and reserva.data_inicio.weekday() == dia
            )

        if not afetada:
            continue

        reserva.carrinho_id = semanal.carrinho_id
        reserva.local_id = semanal.local_id
        reserva.hora_inicio = semanal.hora_inicio
        reserva.hora_fim = semanal.hora_fim


@routes.route(
    "/admin/programacao/editar/<int:programacao_id>",
    methods=["POST"]
)
@admin_required
def editar_programacao(programacao_id):

    semanal = ProgramacaoSemanal.query.get_or_404(programacao_id)

    carrinho_id = request.form.get("carrinho_id", "")
    local_id = request.form.get("local_id", "")
    hora_inicio = request.form.get("hora_inicio", "")
    hora_fim = request.form.get("hora_fim", "")

    destino = "routes.admin_programacao"

    if not (carrinho_id and local_id and hora_inicio and hora_fim):
        return voltar_admin_com_flash(destino, "Preencha todos os campos.")

    try:
        inicio = datetime.strptime(hora_inicio, "%H:%M").time()
        fim = datetime.strptime(hora_fim, "%H:%M").time()
        carrinho_id = int(carrinho_id)
        local_id = int(local_id)
    except ValueError:
        return voltar_admin_com_flash(destino, "Dados inválidos.")

    if inicio >= fim:
        return voltar_admin_com_flash(
            destino,
            "O horário de início deve ser antes do horário de fim."
        )

    if not db.session.get(Carrinho, carrinho_id):
        return voltar_admin_com_flash(destino, "Carrinho não encontrado.")

    if not db.session.get(Local, local_id):
        return voltar_admin_com_flash(destino, "Local não encontrado.")

    # O mesmo carrinho não pode ter dois horários que se sobrepõem no dia
    conflito = (
        ProgramacaoSemanal.query
        .filter(
            ProgramacaoSemanal.id != semanal.id,
            ProgramacaoSemanal.dia_semana == semanal.dia_semana,
            ProgramacaoSemanal.carrinho_id == carrinho_id,
            ProgramacaoSemanal.hora_inicio < fim,
            ProgramacaoSemanal.hora_fim > inicio
        )
        .first()
    )

    if conflito:
        return voltar_admin_com_flash(
            destino,
            "Esse carrinho já tem outro horário que se sobrepõe a este "
            f"na {DIAS_SEMANA[semanal.dia_semana].lower()}: "
            f"{conflito.hora_inicio.strftime('%H:%M')} às "
            f"{conflito.hora_fim.strftime('%H:%M')}."
        )

    # Guarda como era antes para encontrar o que já foi gerado
    antigo = (
        semanal.carrinho_id,
        semanal.hora_inicio,
        semanal.hora_fim
    )

    semanal.carrinho_id = carrinho_id
    semanal.local_id = local_id
    semanal.hora_inicio = inicio
    semanal.hora_fim = fim

    propagar_mudanca_programacao(semanal, antigo)

    db.session.commit()

    return voltar_admin_com_flash(
        destino,
        "Programação atualizada. A agenda de hoje em diante já mostra "
        "o novo local e horário.",
        "success"
    )


# =========================================================
# ADMIN - RESERVAS
# =========================================================

@routes.route("/admin/reservas")
@admin_required
def admin_reservas():

    # Reservas fixas que aparecem na tela principal
    reservas = Reserva.query.filter(
        Reserva.tipo == "FIXA",
        Reserva.ativo == True
    ).order_by(
        Reserva.dia_semana,
        Reserva.hora_inicio
    ).all()

    return render_template(
        "admin_reservas.html",
        reservas=reservas,
        reservas_calendario=dados_do_calendario()
    )