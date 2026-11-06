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
    jsonify,
    abort,
    Response
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
    Participante,
    Reserva,
    ReservaParticipante,
    UsoCarrinho,
    Carrinho,
    Local,
    Pessoa,
    Notificacao,
    HorarioAvulso,
    Campanha,
    CampanhaHorario,
    FotoPessoa,
    DesignacaoTerritorio,
    DirigenteTerritorio
)


routes = Blueprint("routes", __name__)

SAO_PAULO = ZoneInfo("America/Sao_Paulo")

TIPOS_FOTO = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp"
}

TAMANHO_MAXIMO_FOTO = 5 * 1024 * 1024  # 5 MB


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


def voltar_horarios(mensagem, categoria="warning", carrinho_id=None, dia=None):
    """
    Depois de mexer num horário, volta para a tela de onde veio:
    a tela de horários do carrinho (next=carrinho) ou a Programação.
    """
    flash(mensagem, categoria)

    if request.form.get("next") == "carrinho" and carrinho_id:
        return redirect(url_for(
            "routes.admin_carrinho_horarios",
            carrinho_id=carrinho_id,
            dia=dia
        ))

    return redirect(url_for("routes.admin_programacao"))


def pessoas_da_reserva(reserva):

    nomes = [reserva.nome_principal]

    for participante in reserva.participantes:
        nomes.append(participante.nome)

    return nomes


def campanha_do_dia(data):
    """Campanha marcada para esta data (ou None)."""
    return Campanha.query.filter_by(data=data).first()


def reservas_no_horario(data, hora_inicio, hora_fim, carrinho_id=None):
    """
    Reservas ativas que ocupam um horário em uma data.

    Considera:
      - reservas únicas daquele dia;
      - reservas fixas (semanais) que já começaram.
    """

    if campanha_do_dia(data):
        # Dia de campanha: só valem as reservas feitas para este dia.
        # As reservas fixas (semanais) não acontecem neste dia.
        filtro_data = db.and_(
            Reserva.tipo != "FIXA",
            Reserva.data_inicio == data
        )
    else:
        filtro_data = db.or_(
            Reserva.data_inicio == data,
            db.and_(
                Reserva.tipo == "FIXA",
                Reserva.dia_semana == data.weekday(),
                Reserva.data_inicio <= data
            )
        )

    filtros = [
        Reserva.ativo == True,
        Reserva.hora_inicio < hora_fim,
        Reserva.hora_fim > hora_inicio,
        filtro_data
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

    campanha = campanha_do_dia(data)

    if campanha:
        # Dia de campanha: vale SÓ a programação da campanha
        semanais = list(campanha.horarios)
    else:
        # Horários avulsos (só desta data) entram junto com os semanais
        avulsos = HorarioAvulso.query.filter_by(data=data).all()
        semanais = list(semanais) + list(avulsos)

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
            "foto": pessoa.foto_url,
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

        # Carrinho desativado não aparece na agenda
        if not programacao.carrinho.ativo:
            continue

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
        pessoa_menu=pessoa_menu,
        campanha=campanha_do_dia(data_selecionada),
        lembretes=proximas_reservas_da_hora(current_user)
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

    # Reservas ativas em que a pessoa é a principal ou participante
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

    # Reservas fixas (inclusive as que ainda vão começar)
    fixas = [
        r for r in reservas
        if r.tipo == "FIXA"
    ]

    # Reservas únicas de hoje em diante
    proximas = [
        r for r in reservas
        if r.tipo != "FIXA" and r.data_inicio >= hoje
    ]

    return render_template(
        "perfil.html",
        pessoa=pessoa,
        fixas=fixas,
        proximas=proximas,
        pode_editar=pode_editar_perfil(pessoa),
        dias=DIAS_SEMANA,
        hoje=hoje
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

    if extensao not in TIPOS_FOTO:
        flash("Formato inválido. Use PNG, JPG ou WEBP.", "warning")
        return redirect(url_for("routes.perfil", pessoa_id=pessoa.id))

    dados = arquivo.read()

    if not dados:
        flash("A imagem está vazia. Tente outra.", "warning")
        return redirect(url_for("routes.perfil", pessoa_id=pessoa.id))

    if len(dados) > TAMANHO_MAXIMO_FOTO:
        flash("A imagem é muito grande. Use uma de até 5 MB.", "warning")
        return redirect(url_for("routes.perfil", pessoa_id=pessoa.id))

    # Guarda a imagem no banco, que não se perde quando o
    # servidor reinicia (o disco do Render é apagado).
    registro = db.session.get(FotoPessoa, pessoa.id)

    if registro:
        registro.dados = dados
        registro.tipo = TIPOS_FOTO[extensao]
    else:
        db.session.add(FotoPessoa(
            pessoa_id=pessoa.id,
            dados=dados,
            tipo=TIPOS_FOTO[extensao]
        ))

    pessoa.foto = f"db:{uuid.uuid4().hex[:12]}"
    db.session.commit()

    flash("Foto atualizada!", "success")
    return redirect(url_for("routes.perfil", pessoa_id=pessoa.id))


@routes.route("/foto/<int:pessoa_id>")
@login_required
def foto_pessoa(pessoa_id):
    registro = db.session.get(FotoPessoa, pessoa_id)

    if not registro:
        abort(404)

    resposta = Response(registro.dados, mimetype=registro.tipo)

    # O endereço muda a cada foto nova (?v=...), então o
    # navegador pode guardar esta imagem por bastante tempo.
    resposta.headers["Cache-Control"] = "private, max-age=31536000"
    return resposta


@routes.route("/perfil/<int:pessoa_id>/foto/remover", methods=["POST"])
@login_required
def remover_foto(pessoa_id):
    pessoa = Pessoa.query.get_or_404(pessoa_id)

    # Somente o dono do perfil pode remover a própria foto
    if pessoa.usuario_id != current_user.id:
        flash("Você só pode remover a foto do seu próprio perfil.", "danger")
        return redirect(url_for("routes.perfil", pessoa_id=pessoa.id))

    registro = db.session.get(FotoPessoa, pessoa.id)

    if registro:
        db.session.delete(registro)

    pessoa.foto = None
    db.session.commit()

    flash("Foto removida com sucesso.", "success")
    return redirect(url_for("routes.perfil", pessoa_id=pessoa.id))


@routes.route("/perfil/reserva/<int:reserva_id>/excluir", methods=["POST"])
@login_required
def excluir_reserva_perfil(reserva_id):
    reserva = Reserva.query.get_or_404(reserva_id)

    # Somente quem criou a reserva pode excluí-la
    if reserva.usuario_id != current_user.id:
        flash("Você só pode excluir suas próprias reservas.", "danger")
        return redirect(url_for("routes.agenda"))

    if not reserva.ativo:
        flash("Esta reserva já foi cancelada.", "warning")
        return redirect(url_for("routes.agenda"))

    reserva.ativo = False
    db.session.commit()

    flash("Reserva excluída com sucesso.", "success")
    return redirect(url_for("routes.agenda"))

# =========================================================
# NOTIFICAÇÕES
# =========================================================

@routes.route("/notificacoes")
@login_required
def notificacoes():

    criar_lembretes(current_user)

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
        "notificacoes": [dados_notificacao(n) for n in lista]
    })


def toda_semana(dia_semana):
    """'Toda segunda-feira', mas 'Todo sábado' / 'Todo domingo'."""
    nome = DIAS_SEMANA[dia_semana].lower()
    return f"{'Todo' if dia_semana in (5, 6) else 'Toda'} {nome}"


# Lembrete de 1 hora antes: notificação com título "LEMBRETE|reserva|data"
PREFIXO_LEMBRETE = "LEMBRETE|"

MINUTOS_LEMBRETE = 60


def reservas_de_hoje_da_pessoa(nome, data):
    """Reservas ativas de uma pessoa (responsável ou acompanhante) numa data."""

    ids_como_participante = (
        db.session.query(ReservaParticipante.reserva_id)
        .filter(ReservaParticipante.nome == nome)
    )

    candidatas = (
        Reserva.query
        .filter(
            Reserva.ativo == True,
            db.or_(
                Reserva.nome_principal == nome,
                Reserva.id.in_(ids_como_participante)
            )
        )
        .all()
    )

    dia_de_campanha = campanha_do_dia(data) is not None

    resultado = []

    for r in candidatas:
        if r.tipo == "FIXA":
            # Reserva fixa não vale em dia de campanha
            if (
                not dia_de_campanha
                and r.dia_semana == data.weekday()
                and r.data_inicio <= data
            ):
                resultado.append(r)
        elif r.data_inicio == data:
            resultado.append(r)

    return sorted(resultado, key=lambda r: r.hora_inicio)


def proximas_reservas_da_hora(usuario):
    """
    Reservas do usuário que começam daqui a no máximo 1 hora.
    Devolve lista de (reserva, minutos_que_faltam).
    """
    pessoa = Pessoa.query.filter_by(usuario_id=usuario.id).first()

    if not pessoa:
        return []

    agora = agora_sao_paulo().replace(tzinfo=None)
    hoje = agora.date()

    proximas = []

    for r in reservas_de_hoje_da_pessoa(pessoa.nome, hoje):
        inicio = datetime.combine(hoje, r.hora_inicio)
        faltam = (inicio - agora).total_seconds() / 60

        if 0 <= faltam <= MINUTOS_LEMBRETE:
            proximas.append((r, int(faltam)))

    return proximas


def criar_lembretes(usuario):
    """Cria (uma vez só) o lembrete de cada reserva que começa em até 1 hora."""

    hoje = hoje_sao_paulo().isoformat()
    criou = False

    for r, _ in proximas_reservas_da_hora(usuario):
        titulo = f"{PREFIXO_LEMBRETE}{r.id}|{hoje}"

        ja_existe = Notificacao.query.filter_by(
            usuario_id=usuario.id,
            titulo=titulo
        ).first()

        if ja_existe:
            continue

        db.session.add(Notificacao(
            usuario_id=usuario.id,
            reserva_id=r.id,
            titulo=titulo,
            mensagem=(
                f"Você tem carrinho hoje às {r.hora_inicio.strftime('%H:%M')}: "
                f"{r.carrinho.nome} ({r.local.nome})."
            ),
            lida=False,
            criado_em=agora_sao_paulo().replace(tzinfo=None)
        ))

        criou = True

    if criou:
        db.session.commit()


# Avisos do admin são gravados como notificação comum,
# com o título no formato "AVISO|remetente|título".
PREFIXO_AVISO = "AVISO|"


def ler_aviso(titulo):
    """Devolve (remetente, título) se for aviso do admin, senão None."""
    if not titulo or not titulo.startswith(PREFIXO_AVISO):
        return None

    partes = titulo.split("|", 2)

    if len(partes) < 3:
        return None

    return partes[1], partes[2]


def dados_notificacao(notificacao):

    criado = (
        notificacao.criado_em.strftime("%d/%m %H:%M")
        if notificacao.criado_em
        else ""
    )

    dados = {
        "id": notificacao.id,
        "lida": notificacao.lida,
        "criado_em": criado,
        "tipo": "outro",
        "titulo": notificacao.titulo,
        "mensagem": notificacao.mensagem,
        "detalhes": None,
        "rodape": f"Enviado em {criado}" if criado else ""
    }

    # Lembrete de 1 hora antes
    if notificacao.titulo and notificacao.titulo.startswith(PREFIXO_LEMBRETE) and notificacao.reserva:
        r = notificacao.reserva
        dados["tipo"] = "lembrete"
        dados["titulo"] = f"Você tem carrinho hoje às {r.hora_inicio.strftime('%H:%M')}!"
        dados["mensagem"] = ""
        dados["detalhes"] = {
            "quando": "Hoje",
            "horario": f"{r.hora_inicio.strftime('%H:%M')} às {r.hora_fim.strftime('%H:%M')}",
            "carrinho": r.carrinho.nome,
            "local": r.local.nome,
            "cancelada": not r.ativo
        }
        dados["rodape"] = f"Lembrete • {criado}"
        return dados

    aviso = ler_aviso(notificacao.titulo)

    if aviso:
        remetente, titulo = aviso
        dados["tipo"] = "aviso"
        dados["titulo"] = titulo
        dados["rodape"] = f"Enviado por {remetente} • {criado}"
        return dados

    reserva = notificacao.reserva

    if reserva:
        # Convite: mostra os detalhes da própria reserva
        dados["tipo"] = "convite"
        dados["titulo"] = f"{reserva.nome_principal} te chamou para o carrinho!"
        dados["mensagem"] = ""

        if reserva.tipo == "FIXA" and reserva.dia_semana is not None:
            quando = toda_semana(reserva.dia_semana)
            if reserva.data_inicio > hoje_sao_paulo():
                quando += f" (a partir de {reserva.data_inicio.strftime('%d/%m')})"
        else:
            quando = (
                f"{DIAS_SEMANA[reserva.data_inicio.weekday()]}, "
                f"{reserva.data_inicio.strftime('%d/%m')}"
            )

        dados["detalhes"] = {
            "quando": quando,
            "horario": (
                f"{reserva.hora_inicio.strftime('%H:%M')} às "
                f"{reserva.hora_fim.strftime('%H:%M')}"
            ),
            "carrinho": reserva.carrinho.nome,
            "local": reserva.local.nome,
            "cancelada": not reserva.ativo
        }

    return dados


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

    # Impede reserva em carrinho desativado
    if not programacao.carrinho.ativo:
        return voltar_agenda_com_flash(
            programacao.data,
            "Este carrinho está desativado e não aceita reservas.",
            "warning"
        )

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

        # Em dia de campanha não dá para criar reserva semanal
        if reserva_fixa and campanha_do_dia(programacao.data):
            return voltar_agenda_com_flash(
                programacao.data,
                "Este é um dia de campanha: a reserva vale só para este dia. "
                "Desmarque a opção de reserva fixa.",
                "warning"
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
                        f"{nome_principal} te chamou para o carrinho: "
                        f"{DIAS_SEMANA[programacao.data.weekday()]}, "
                        f"{programacao.data.strftime('%d/%m')}, "
                        f"{programacao.hora_inicio.strftime('%H:%M')} às "
                        f"{programacao.hora_fim.strftime('%H:%M')}, "
                        f"{programacao.carrinho.nome} ({programacao.local.nome})."
                    ),
                    # Hora de Brasília (o padrão do banco grava em UTC)
                    criado_em=agora_sao_paulo().replace(tzinfo=None),
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

    # Cancelada pela tela de Reservas do admin: volta para ela
    if request.form.get("next") == "admin":
        flash("Reserva cancelada.", "success")
        return redirect(url_for(
            "routes.admin_reservas",
            aba=request.form.get("aba", "proximas")
        ))

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
        .order_by(Carrinho.ativo.desc(), Carrinho.id)
        .all()
    )

    # Resumo de cada carrinho: quantos horários e em quantos dias
    resumo = {}

    for semanal in ProgramacaoSemanal.query.all():
        item = resumo.setdefault(
            semanal.carrinho_id,
            {"horarios": 0, "dias": set()}
        )
        item["horarios"] += 1
        item["dias"].add(semanal.dia_semana)

    return render_template(
        "admin_carrinhos.html",
        carrinhos=carrinhos,
        resumo=resumo
    )


DIAS_CURTOS = ["SEG", "TER", "QUA", "QUI", "SEX", "SÁB", "DOM"]


@routes.route("/admin/carrinhos/<int:carrinho_id>")
@admin_required
def admin_carrinho_horarios(carrinho_id):

    carrinho = Carrinho.query.get_or_404(carrinho_id)

    todos = (
        ProgramacaoSemanal.query
        .filter_by(carrinho_id=carrinho.id)
        .order_by(
            ProgramacaoSemanal.dia_semana,
            ProgramacaoSemanal.hora_inicio
        )
        .all()
    )

    # Horários separados por dia (para as abas SEG, TER...)
    por_dia = {numero: [] for numero in range(7)}

    for semanal in todos:
        por_dia[semanal.dia_semana].append(semanal)

    # Dia escolhido: o da URL, ou hoje
    dia = request.args.get("dia", type=int)

    if dia not in range(7):
        dia = hoje_sao_paulo().weekday()

    horarios_do_dia = por_dia[dia]

    locais_do_dia = []

    for semanal in horarios_do_dia:
        if semanal.local.nome not in locais_do_dia:
            locais_do_dia.append(semanal.local.nome)

    locais = (
        Local.query
        .filter_by(ativo=True)
        .order_by(Local.nome)
        .all()
    )

    # Datas especiais (horários avulsos) de hoje em diante
    avulsos = (
        HorarioAvulso.query
        .filter(
            HorarioAvulso.carrinho_id == carrinho.id,
            HorarioAvulso.data >= hoje_sao_paulo()
        )
        .order_by(HorarioAvulso.data, HorarioAvulso.hora_inicio)
        .all()
    )

    return render_template(
        "admin_carrinho_horarios.html",
        avulsos=avulsos,
        hoje=hoje_sao_paulo(),
        carrinho=carrinho,
        dia=dia,
        por_dia=por_dia,
        horarios=horarios_do_dia,
        locais_do_dia=locais_do_dia,
        locais=locais,
        dias=DIAS_SEMANA,
        dias_curtos=DIAS_CURTOS,
        total=len(todos)
    )


@routes.route(
    "/admin/carrinhos/adicionar",
    methods=["POST"]
)
@admin_required
def adicionar_carrinho():

    nome = request.form.get("nome", "").strip()

    if not nome:
        return voltar_admin_com_flash(
            "routes.admin_carrinhos",
            "Informe o nome do carrinho."
        )

    if Carrinho.query.filter_by(nome=nome).first():
        return voltar_admin_com_flash(
            "routes.admin_carrinhos",
            "Já existe um carrinho com esse nome."
        )

    db.session.add(Carrinho(nome=nome, ativo=True))
    db.session.commit()

    return voltar_admin_com_flash(
        "routes.admin_carrinhos",
        f"Carrinho “{nome}” criado! Agora adicione os horários dele.",
        "success"
    )


@routes.route(
    "/admin/carrinhos/editar/<int:carrinho_id>",
    methods=["POST"]
)
@admin_required
def editar_carrinho(carrinho_id):

    carrinho = Carrinho.query.get_or_404(carrinho_id)

    nome = request.form.get("nome", "").strip()

    if not nome:
        return voltar_admin_com_flash(
            "routes.admin_carrinhos",
            "Informe o nome do carrinho."
        )

    outro_carrinho = (
        Carrinho.query
        .filter(
            Carrinho.nome == nome,
            Carrinho.id != carrinho.id
        )
        .first()
    )

    if outro_carrinho:
        return voltar_admin_com_flash(
            "routes.admin_carrinhos",
            "Já existe um carrinho com esse nome."
        )

    carrinho.nome = nome

    db.session.commit()

    if request.form.get("next") == "carrinho":
        flash("Nome atualizado.", "success")
        return redirect(url_for(
            "routes.admin_carrinho_horarios",
            carrinho_id=carrinho.id
        ))

    return voltar_admin_com_flash(
        "routes.admin_carrinhos",
        "Nome atualizado.",
        "success"
    )


@routes.route(
    "/admin/carrinhos/alternar/<int:carrinho_id>",
    methods=["POST"]
)
@admin_required
def alternar_carrinho(carrinho_id):

    carrinho = Carrinho.query.get_or_404(carrinho_id)

    carrinho.ativo = not carrinho.ativo

    db.session.commit()

    mensagem = (
        f"{carrinho.nome} ativado."
        if carrinho.ativo
        else f"{carrinho.nome} desativado. Ele não aparece mais na agenda."
    )

    if request.form.get("next") == "carrinho":
        flash(mensagem, "success")
        return redirect(url_for(
            "routes.admin_carrinho_horarios",
            carrinho_id=carrinho.id
        ))

    return voltar_admin_com_flash(
        "routes.admin_carrinhos",
        mensagem,
        "success"
    )


# =========================================================
# ADMIN - PESSOAS
# =========================================================

@routes.route("/admin/pessoas")
@admin_required
def admin_pessoas():

    pessoas = (
        Pessoa.query
        .order_by(Pessoa.ativo.desc(), Pessoa.nome)
        .all()
    )

    com_login = sum(
        1 for p in pessoas
        if p.usuario and p.usuario.ativo
    )

    return render_template(
        "admin_pessoas.html",
        pessoas=pessoas,
        com_login=com_login,
        meu_usuario_id=current_user.id
    )


def _marcado(valor):
    return valor.strip().lower() in ["sim", "on", "true", "1", "yes"]


@routes.route(
    "/admin/pessoas/adicionar",
    methods=["POST"]
)
@admin_required
def adicionar_pessoa():

    destino = "routes.admin_pessoas"

    nome = request.form.get("nome", "").strip()

    permitir_acesso = _marcado(
        request.form.get("permitir_acesso", "")
    )

    email = request.form.get("email", "").strip().lower()
    senha = request.form.get("senha", "")
    tipo = request.form.get("tipo", "IRMAO").strip().upper()

    if tipo not in ["IRMAO", "ADMIN"]:
        tipo = "IRMAO"

    if not nome:
        return voltar_admin_com_flash(destino, "Informe o nome do irmão.")

    if Pessoa.query.filter(db.func.lower(Pessoa.nome) == nome.lower()).first():
        return voltar_admin_com_flash(destino, f"{nome} já está cadastrado.")

    if permitir_acesso:

        if not email:
            return voltar_admin_com_flash(
                destino, "Informe o e-mail para criar o acesso ao TPL."
            )

        if not senha:
            return voltar_admin_com_flash(
                destino, "Informe uma senha inicial para criar o acesso."
            )

        if Usuario.query.filter_by(email=email).first():
            return voltar_admin_com_flash(
                destino, "Este e-mail já está cadastrado."
            )

    pessoa = Pessoa(nome=nome, ativo=True)
    db.session.add(pessoa)

    if permitir_acesso:

        usuario = Usuario(
            nome=nome,
            email=email,
            senha=generate_password_hash(senha),
            tipo=tipo,
            ativo=True
        )

        db.session.add(usuario)

        # Obtém o ID do usuário antes de associá-lo à pessoa.
        db.session.flush()

        pessoa.usuario_id = usuario.id

    db.session.commit()

    return voltar_admin_com_flash(
        destino,
        f"{nome} foi adicionado. Para dar acesso ao app, toque em ⋯ ao lado do nome.",
        "success"
    )


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

    # Trava: o admin não pode tirar o próprio acesso
    eh_eu = pessoa.usuario_id is not None and pessoa.usuario_id == current_user.id

    if eh_eu:
        if not permitir_acesso:
            return voltar_admin_com_flash(
                "routes.admin_pessoas",
                "Você não pode tirar o seu próprio acesso ao app."
            )

        if tipo != "ADMIN":
            return voltar_admin_com_flash(
                "routes.admin_pessoas",
                "Você não pode tirar o seu próprio acesso de administrador."
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

    if pessoa.ativo and pessoa.usuario_id == current_user.id:
        return voltar_admin_com_flash(
            "routes.admin_pessoas",
            "Você não pode desativar o seu próprio cadastro."
        )

    pessoa.ativo = not pessoa.ativo

    db.session.commit()

    return voltar_admin_com_flash(
        "routes.admin_pessoas",
        f"{pessoa.nome} ativado."
        if pessoa.ativo
        else f"{pessoa.nome} desativado. Não aparece mais para escolher nas reservas.",
        "success"
    )


@routes.route(
    "/admin/pessoas/excluir/<int:pessoa_id>",
    methods=["POST"]
)
@admin_required
def excluir_pessoa(pessoa_id):

    pessoa = Pessoa.query.get_or_404(pessoa_id)

    if pessoa.usuario_id == current_user.id:
        return voltar_admin_com_flash(
            "routes.admin_pessoas",
            "Você não pode excluir o seu próprio cadastro."
        )

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

    # O histórico de territórios guarda o nome do dirigente,
    # então só desfaz a ligação com a pessoa.
    DesignacaoTerritorio.query.filter_by(pessoa_id=pessoa.id).update(
        {"pessoa_id": None}, synchronize_session=False
    )

    DirigenteTerritorio.query.filter_by(pessoa_id=pessoa.id).delete(
        synchronize_session=False
    )

    if usuario:
        DesignacaoTerritorio.query.filter_by(criado_por_id=usuario.id).update(
            {"criado_por_id": None}, synchronize_session=False
        )

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
        .order_by(Local.ativo.desc(), Local.nome)
        .all()
    )

    hoje = hoje_sao_paulo()

    # Quais carrinhos usam cada local hoje em dia
    # (horários semanais + datas especiais que ainda vão acontecer)
    carrinhos_por_local = {}

    for semanal in ProgramacaoSemanal.query.all():
        carrinhos_por_local.setdefault(semanal.local_id, set()).add(semanal.carrinho_id)

    for avulso in HorarioAvulso.query.filter(HorarioAvulso.data >= hoje).all():
        carrinhos_por_local.setdefault(avulso.local_id, set()).add(avulso.carrinho_id)

    # Quantos horários e reservas cada local tem
    # (para avisar na confirmação de exclusão)
    def contar(modelo):
        return dict(
            db.session.query(modelo.local_id, db.func.count(modelo.id))
            .group_by(modelo.local_id)
            .all()
        )

    semanais_por_local = contar(ProgramacaoSemanal)
    avulsos_por_local = contar(HorarioAvulso)
    campanha_por_local = contar(CampanhaHorario)
    reservas_por_local = contar(Reserva)

    resumo = {
        local.id: {
            "carrinhos": len(carrinhos_por_local.get(local.id, ())),
            "horarios": (
                semanais_por_local.get(local.id, 0)
                + avulsos_por_local.get(local.id, 0)
                + campanha_por_local.get(local.id, 0)
            ),
            "reservas": reservas_por_local.get(local.id, 0)
        }
        for local in locais
    }

    return render_template(
        "admin_locais.html",
        locais=locais,
        resumo=resumo
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
    nome = local.nome

    # Excluir o local apaga TUDO o que está nele:
    # horários semanais, datas especiais, horários da agenda
    # e reservas (com participantes, check-ins e notificações).

    reserva_ids = [
        r.id for r in Reserva.query.filter_by(local_id=local.id).all()
    ]

    if reserva_ids:
        ReservaParticipante.query.filter(
            ReservaParticipante.reserva_id.in_(reserva_ids)
        ).delete(synchronize_session=False)

        UsoCarrinho.query.filter(
            UsoCarrinho.reserva_id.in_(reserva_ids)
        ).delete(synchronize_session=False)

        Notificacao.query.filter(
            Notificacao.reserva_id.in_(reserva_ids)
        ).delete(synchronize_session=False)

        Reserva.query.filter(
            Reserva.id.in_(reserva_ids)
        ).delete(synchronize_session=False)

    programacao_ids = [
        p.id for p in Programacao.query.filter_by(local_id=local.id).all()
    ]

    if programacao_ids:
        inscricao_ids = [
            i.id for i in Inscricao.query.filter(
                Inscricao.programacao_id.in_(programacao_ids)
            ).all()
        ]

        if inscricao_ids:
            Participante.query.filter(
                Participante.inscricao_id.in_(inscricao_ids)
            ).delete(synchronize_session=False)

            Inscricao.query.filter(
                Inscricao.id.in_(inscricao_ids)
            ).delete(synchronize_session=False)

        Programacao.query.filter(
            Programacao.id.in_(programacao_ids)
        ).delete(synchronize_session=False)

    ProgramacaoSemanal.query.filter_by(
        local_id=local.id
    ).delete(synchronize_session=False)

    HorarioAvulso.query.filter_by(
        local_id=local.id
    ).delete(synchronize_session=False)

    CampanhaHorario.query.filter_by(
        local_id=local.id
    ).delete(synchronize_session=False)

    Campanha.query.filter_by(
        local_id=local.id
    ).update({"local_id": None}, synchronize_session=False)

    db.session.delete(local)
    db.session.commit()

    return voltar_admin_com_flash(
        "routes.admin_locais",
        f"Local “{nome}” excluído, junto com os horários e reservas dele.",
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

    carrinho_id = request.form.get("carrinho_id", "") or str(semanal.carrinho_id)
    local_id = request.form.get("local_id", "")
    hora_inicio = request.form.get("hora_inicio", "")
    hora_fim = request.form.get("hora_fim", "")
    quantidade_maxima = request.form.get("quantidade_maxima", "")

    cid = semanal.carrinho_id
    dia = semanal.dia_semana

    if not (local_id and hora_inicio and hora_fim):
        return voltar_horarios("Preencha todos os campos.", carrinho_id=cid, dia=dia)

    try:
        inicio = datetime.strptime(hora_inicio, "%H:%M").time()
        fim = datetime.strptime(hora_fim, "%H:%M").time()
        carrinho_id = int(carrinho_id)
        local_id = int(local_id)
        quantidade_maxima = (
            int(quantidade_maxima)
            if quantidade_maxima
            else semanal.quantidade_maxima
        )
    except ValueError:
        return voltar_horarios("Dados inválidos.", carrinho_id=cid, dia=dia)

    if quantidade_maxima < 1:
        return voltar_horarios(
            "O número de vagas deve ser pelo menos 1.",
            carrinho_id=cid, dia=dia
        )

    if inicio >= fim:
        return voltar_horarios(
            "O horário de início deve ser antes do horário de fim.",
            carrinho_id=cid, dia=dia
        )

    if not db.session.get(Carrinho, carrinho_id):
        return voltar_horarios("Carrinho não encontrado.", carrinho_id=cid, dia=dia)

    if not db.session.get(Local, local_id):
        return voltar_horarios("Local não encontrado.", carrinho_id=cid, dia=dia)

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
        return voltar_horarios(
            "Esse carrinho já tem outro horário que se sobrepõe a este "
            f"na {DIAS_SEMANA[semanal.dia_semana].lower()}: "
            f"{conflito.hora_inicio.strftime('%H:%M')} às "
            f"{conflito.hora_fim.strftime('%H:%M')}.",
            carrinho_id=cid, dia=dia
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
    semanal.quantidade_maxima = quantidade_maxima

    propagar_mudanca_programacao(semanal, antigo)

    db.session.commit()

    return voltar_horarios(
        "Horário atualizado. A agenda de hoje em diante já mostra "
        "a mudança.",
        "success",
        carrinho_id, dia
    )


@routes.route(
    "/admin/programacao/adicionar",
    methods=["POST"]
)
@admin_required
def adicionar_programacao():

    # "Só em uma data": cria um horário avulso
    if request.form.get("repetir") == "data":
        return adicionar_horario_avulso()

    # Aceita vários dias de uma vez (caixinhas "dias")
    # ou um dia só (campo "dia_semana", da tela de Programação).
    dias_escolhidos = request.form.getlist("dias")

    if not dias_escolhidos and request.form.get("dia_semana"):
        dias_escolhidos = [request.form.get("dia_semana")]

    carrinho_id = request.form.get("carrinho_id", "")
    local_id = request.form.get("local_id", "")
    hora_inicio = request.form.get("hora_inicio", "")
    hora_fim = request.form.get("hora_fim", "")
    quantidade_maxima = request.form.get("quantidade_maxima", "3")

    try:
        cid = int(carrinho_id)
    except ValueError:
        cid = None

    dia_volta = request.form.get("dia_atual", type=int)

    if not dias_escolhidos:
        return voltar_horarios(
            "Escolha pelo menos um dia da semana.",
            carrinho_id=cid, dia=dia_volta
        )

    if not (carrinho_id and local_id and hora_inicio and hora_fim):
        return voltar_horarios(
            "Preencha todos os campos.",
            carrinho_id=cid, dia=dia_volta
        )

    try:
        dias = sorted({int(d) for d in dias_escolhidos})
        inicio = datetime.strptime(hora_inicio, "%H:%M").time()
        fim = datetime.strptime(hora_fim, "%H:%M").time()
        carrinho_id = int(carrinho_id)
        local_id = int(local_id)
        quantidade_maxima = int(quantidade_maxima or 3)
    except ValueError:
        return voltar_horarios("Dados inválidos.", carrinho_id=cid, dia=dia_volta)

    if any(d not in range(7) for d in dias):
        return voltar_horarios(
            "Dia da semana inválido.",
            carrinho_id=cid, dia=dia_volta
        )

    if quantidade_maxima < 1:
        return voltar_horarios(
            "O número de vagas deve ser pelo menos 1.",
            carrinho_id=cid, dia=dia_volta
        )

    if inicio >= fim:
        return voltar_horarios(
            "O horário de início deve ser antes do horário de fim.",
            carrinho_id=cid, dia=dia_volta
        )

    carrinho = db.session.get(Carrinho, carrinho_id)

    if not carrinho:
        return voltar_horarios("Carrinho não encontrado.")

    if not db.session.get(Local, local_id):
        return voltar_horarios(
            "Local não encontrado.",
            carrinho_id=cid, dia=dia_volta
        )

    # Confere todos os dias antes de gravar qualquer um
    for dia in dias:

        conflito = (
            ProgramacaoSemanal.query
            .filter(
                ProgramacaoSemanal.dia_semana == dia,
                ProgramacaoSemanal.carrinho_id == carrinho_id,
                ProgramacaoSemanal.hora_inicio < fim,
                ProgramacaoSemanal.hora_fim > inicio
            )
            .first()
        )

        if conflito:
            return voltar_horarios(
                "Nada foi salvo: esse carrinho já tem um horário que se "
                f"sobrepõe na {DIAS_SEMANA[dia].lower()} "
                f"({conflito.hora_inicio.strftime('%H:%M')} às "
                f"{conflito.hora_fim.strftime('%H:%M')}).",
                carrinho_id=cid, dia=dia_volta
            )

    for dia in dias:
        db.session.add(ProgramacaoSemanal(
            dia_semana=dia,
            hora_inicio=inicio,
            hora_fim=fim,
            carrinho_id=carrinho_id,
            local_id=local_id,
            quantidade_maxima=quantidade_maxima
        ))

    db.session.commit()

    nomes_dias = ", ".join(DIAS_SEMANA[d].lower() for d in dias)

    # Volta para o dia que estava aberto, se ele recebeu o horário;
    # senão, para o primeiro dia adicionado.
    dia_destino = dia_volta if dia_volta in dias else dias[0]

    return voltar_horarios(
        f"Horário adicionado: {nomes_dias}, das "
        f"{inicio.strftime('%H:%M')} às {fim.strftime('%H:%M')}. "
        "Já aparece na agenda.",
        "success",
        carrinho_id, dia_destino
    )


@routes.route(
    "/admin/programacao/remover/<int:programacao_id>",
    methods=["POST"]
)
@admin_required
def remover_programacao(programacao_id):

    semanal = ProgramacaoSemanal.query.get_or_404(programacao_id)

    cid = semanal.carrinho_id
    dia = semanal.dia_semana
    hoje = hoje_sao_paulo()

    # Não deixa remover se houver reserva ativa nesse horário
    # (fixas, ou únicas de hoje em diante, no mesmo dia da semana)
    reservas = (
        Reserva.query
        .filter(
            Reserva.ativo == True,
            Reserva.carrinho_id == semanal.carrinho_id,
            Reserva.hora_inicio < semanal.hora_fim,
            Reserva.hora_fim > semanal.hora_inicio
        )
        .all()
    )

    for reserva in reservas:

        if reserva.tipo == "FIXA":
            afetada = reserva.dia_semana == semanal.dia_semana
        else:
            afetada = (
                reserva.data_inicio >= hoje
                and reserva.data_inicio.weekday() == semanal.dia_semana
            )

        if afetada:
            return voltar_horarios(
                "Não é possível remover este horário porque ele tem "
                "reservas ativas. Cancele as reservas primeiro.",
                "danger",
                cid, dia
            )

    chave = (semanal.carrinho_id, semanal.hora_inicio, semanal.hora_fim)

    db.session.delete(semanal)

    # Remove da agenda os horários já gerados de hoje em diante
    geradas = (
        Programacao.query
        .filter(
            Programacao.data >= hoje,
            Programacao.carrinho_id == chave[0],
            Programacao.hora_inicio == chave[1],
            Programacao.hora_fim == chave[2]
        )
        .all()
    )

    for programacao in geradas:

        if programacao.data.weekday() != dia:
            continue

        if Inscricao.query.filter_by(programacao_id=programacao.id).first():
            continue

        db.session.delete(programacao)

    db.session.commit()

    return voltar_horarios("Horário removido.", "success", cid, dia)


def adicionar_horario_avulso():
    """Cria um horário que vale só para uma data (chamada por adicionar_programacao)."""

    carrinho_id = request.form.get("carrinho_id", "")
    local_id = request.form.get("local_id", "")
    data_texto = request.form.get("data", "")
    hora_inicio = request.form.get("hora_inicio", "")
    hora_fim = request.form.get("hora_fim", "")
    quantidade_maxima = request.form.get("quantidade_maxima", "3")

    dia_volta = request.form.get("dia_atual", type=int)

    try:
        cid = int(carrinho_id)
    except ValueError:
        cid = None

    if not (carrinho_id and local_id and data_texto and hora_inicio and hora_fim):
        return voltar_horarios(
            "Escolha a data e preencha todos os campos.",
            carrinho_id=cid, dia=dia_volta
        )

    try:
        data = date.fromisoformat(data_texto)
        inicio = datetime.strptime(hora_inicio, "%H:%M").time()
        fim = datetime.strptime(hora_fim, "%H:%M").time()
        carrinho_id = int(carrinho_id)
        local_id = int(local_id)
        quantidade_maxima = int(quantidade_maxima or 3)
    except ValueError:
        return voltar_horarios("Dados inválidos.", carrinho_id=cid, dia=dia_volta)

    if data < hoje_sao_paulo():
        return voltar_horarios(
            "Escolha uma data de hoje em diante.",
            carrinho_id=cid, dia=dia_volta
        )

    if quantidade_maxima < 1:
        return voltar_horarios(
            "O número de vagas deve ser pelo menos 1.",
            carrinho_id=cid, dia=dia_volta
        )

    if inicio >= fim:
        return voltar_horarios(
            "O horário de início deve ser antes do horário de fim.",
            carrinho_id=cid, dia=dia_volta
        )

    carrinho = db.session.get(Carrinho, carrinho_id)

    if not carrinho:
        return voltar_horarios("Carrinho não encontrado.")

    if not db.session.get(Local, local_id):
        return voltar_horarios(
            "Local não encontrado.",
            carrinho_id=cid, dia=dia_volta
        )

    data_br = data.strftime("%d/%m/%Y")

    # Não pode bater com um horário semanal desse carrinho nesse dia
    conflito = (
        ProgramacaoSemanal.query
        .filter(
            ProgramacaoSemanal.dia_semana == data.weekday(),
            ProgramacaoSemanal.carrinho_id == carrinho_id,
            ProgramacaoSemanal.hora_inicio < fim,
            ProgramacaoSemanal.hora_fim > inicio
        )
        .first()
    )

    # Nem com outro horário avulso desse carrinho na mesma data
    if not conflito:
        conflito = (
            HorarioAvulso.query
            .filter(
                HorarioAvulso.data == data,
                HorarioAvulso.carrinho_id == carrinho_id,
                HorarioAvulso.hora_inicio < fim,
                HorarioAvulso.hora_fim > inicio
            )
            .first()
        )

    if conflito:
        return voltar_horarios(
            f"Nada foi salvo: em {data_br} esse carrinho já tem um horário "
            f"que se sobrepõe ({conflito.hora_inicio.strftime('%H:%M')} às "
            f"{conflito.hora_fim.strftime('%H:%M')}).",
            carrinho_id=cid, dia=dia_volta
        )

    db.session.add(HorarioAvulso(
        data=data,
        hora_inicio=inicio,
        hora_fim=fim,
        carrinho_id=carrinho_id,
        local_id=local_id,
        quantidade_maxima=quantidade_maxima
    ))

    db.session.commit()

    return voltar_horarios(
        f"Horário adicionado só em {data_br}, das "
        f"{inicio.strftime('%H:%M')} às {fim.strftime('%H:%M')}. "
        "Já aparece na agenda nesse dia.",
        "success",
        carrinho_id, dia_volta
    )


@routes.route(
    "/admin/horario-avulso/remover/<int:avulso_id>",
    methods=["POST"]
)
@admin_required
def remover_horario_avulso(avulso_id):

    avulso = HorarioAvulso.query.get_or_404(avulso_id)

    cid = avulso.carrinho_id
    dia_volta = request.form.get("dia_atual", type=int)

    # Não deixa remover se já houver reserva nesse horário
    if reservas_no_horario(
        avulso.data,
        avulso.hora_inicio,
        avulso.hora_fim,
        avulso.carrinho_id
    ):
        return voltar_horarios(
            "Não é possível remover este horário porque ele tem "
            "reservas ativas. Cancele as reservas primeiro.",
            "danger",
            cid, dia_volta
        )

    # Remove também o horário que já tinha sido gerado na agenda
    gerada = (
        Programacao.query
        .filter_by(
            data=avulso.data,
            carrinho_id=avulso.carrinho_id,
            hora_inicio=avulso.hora_inicio,
            hora_fim=avulso.hora_fim
        )
        .first()
    )

    if gerada and not Inscricao.query.filter_by(programacao_id=gerada.id).first():
        db.session.delete(gerada)

    db.session.delete(avulso)
    db.session.commit()

    return voltar_horarios("Horário da data especial removido.", "success", cid, dia_volta)


# =========================================================
# ADMIN - RESERVAS
# =========================================================

@routes.route("/admin/reservas")
@admin_required
def admin_reservas():

    hoje = hoje_sao_paulo()

    ativas = (
        Reserva.query
        .filter(Reserva.ativo == True)
        .all()
    )

    # Próximas: reservas únicas de hoje em diante (mais perto primeiro)
    proximas = sorted(
        (r for r in ativas if r.tipo != "FIXA" and r.data_inicio >= hoje),
        key=lambda r: (r.data_inicio, r.hora_inicio)
    )

    # Fixas: semanais, por dia da semana
    fixas = sorted(
        (r for r in ativas if r.tipo == "FIXA"),
        key=lambda r: (
            r.dia_semana if r.dia_semana is not None else 9,
            r.hora_inicio
        )
    )

    # Realizadas: únicas que já passaram e não foram canceladas
    realizadas = [
        r for r in ativas
        if r.tipo != "FIXA" and r.data_inicio < hoje
    ]

    # ----- Resumo -----

    # Reservas por local
    por_local = {}

    for r in realizadas:
        por_local[r.local.nome] = por_local.get(r.local.nome, 0) + 1

    locais_usados = sorted(por_local.items(), key=lambda item: (-item[1], item[0]))

    locais_sem_uso = [
        local.nome
        for local in Local.query.filter_by(ativo=True).order_by(Local.nome)
        if local.nome not in por_local
    ]

    # Participação de cada irmão (responsável ou acompanhante)
    por_pessoa = {}

    for r in realizadas:
        for nome in pessoas_da_reserva(r):
            por_pessoa[nome] = por_pessoa.get(nome, 0) + 1

    top_pessoas = sorted(por_pessoa.items(), key=lambda item: (-item[1], item[0]))[:5]

    irmaos = [p.nome for p in Pessoa.query.filter_by(ativo=True).order_by(Pessoa.nome)]

    irmaos_participaram = [nome for nome in irmaos if nome in por_pessoa]
    irmaos_nao_participaram = [nome for nome in irmaos if nome not in por_pessoa]

    resumo = {
        "total": len(realizadas),
        "irmaos_total": len(irmaos),
        "irmaos_participaram": len(irmaos_participaram),
        "irmaos_nao_participaram": irmaos_nao_participaram,
        "locais_usados": locais_usados,
        "locais_sem_uso": locais_sem_uso,
        "top_pessoas": top_pessoas,
    }

    aba = request.args.get("aba", "proximas")

    if aba == "antigas":
        aba = "resumo"

    if aba not in ("proximas", "fixas", "resumo"):
        aba = "proximas"

    return render_template(
        "admin_reservas.html",
        proximas=proximas,
        fixas=fixas,
        resumo=resumo,
        aba=aba,
        hoje=hoje,
        dias=DIAS_SEMANA,
        dias_curtos=DIAS_CURTOS
    )


# =========================================================
# ADMIN - CAMPANHAS
# Dias com programação própria (ex.: Finados)
# =========================================================

def _ler_hora(texto):
    return datetime.strptime(texto, "%H:%M").time()


def _reservas_fora_da_campanha(campanha):
    """
    Reservas únicas deste dia que estão em horários que NÃO são
    da campanha (foram feitas antes da campanha existir).
    """
    reservas = (
        Reserva.query
        .filter(
            Reserva.ativo == True,
            Reserva.tipo != "FIXA",
            Reserva.data_inicio == campanha.data
        )
        .all()
    )

    chaves = {
        (h.carrinho_id, h.hora_inicio, h.hora_fim)
        for h in campanha.horarios
    }

    return [
        r for r in reservas
        if (r.carrinho_id, r.hora_inicio, r.hora_fim) not in chaves
    ]


def _reservas_do_horario_campanha(horario):
    return reservas_no_horario(
        horario.campanha.data,
        horario.hora_inicio,
        horario.hora_fim,
        horario.carrinho_id
    )


def _voltar_campanha(campanha_id, mensagem, categoria="warning"):
    flash(mensagem, categoria)
    return redirect(url_for("routes.admin_campanha", campanha_id=campanha_id))


@routes.route("/admin/campanhas")
@admin_required
def admin_campanhas():

    hoje = hoje_sao_paulo()

    campanhas = Campanha.query.order_by(Campanha.data).all()

    proximas = [c for c in campanhas if c.data >= hoje]
    passadas = [c for c in campanhas if c.data < hoje][::-1]

    return render_template(
        "admin_campanhas.html",
        locais=Local.query.filter_by(ativo=True).order_by(Local.nome).all(),
        proximas=proximas,
        passadas=passadas,
        hoje=hoje,
        dias_curtos=DIAS_CURTOS
    )


@routes.route("/admin/campanhas/criar", methods=["POST"])
@admin_required
def criar_campanha():

    destino = "routes.admin_campanhas"

    nome = request.form.get("nome", "").strip()
    data_texto = request.form.get("data", "")
    local_id = request.form.get("local_id", type=int)

    if not nome or not data_texto:
        return voltar_admin_com_flash(destino, "Informe o nome e a data da campanha.")

    if not local_id or not db.session.get(Local, local_id):
        return voltar_admin_com_flash(destino, "Escolha o local da campanha.")

    try:
        data = date.fromisoformat(data_texto)
    except ValueError:
        return voltar_admin_com_flash(destino, "Data inválida.")

    if data < hoje_sao_paulo():
        return voltar_admin_com_flash(destino, "Escolha uma data de hoje em diante.")

    existente = Campanha.query.filter_by(data=data).first()

    if existente:
        return voltar_admin_com_flash(
            destino,
            f"Já existe a campanha “{existente.nome}” em {data.strftime('%d/%m/%Y')}."
        )

    campanha = Campanha(nome=nome, data=data, local_id=local_id)
    db.session.add(campanha)
    db.session.commit()

    return _voltar_campanha(
        campanha.id,
        f"Campanha “{nome}” criada! Agora monte os horários dela "
        "(ou copie a programação normal do dia).",
        "success"
    )


@routes.route("/admin/campanhas/<int:campanha_id>")
@admin_required
def admin_campanha(campanha_id):

    campanha = Campanha.query.get_or_404(campanha_id)

    carrinhos = (
        Carrinho.query
        .filter_by(ativo=True)
        .order_by(Carrinho.id)
        .all()
    )

    por_carrinho = {c.id: [] for c in carrinhos}

    for horario in campanha.horarios:
        por_carrinho.setdefault(horario.carrinho_id, []).append(horario)

    # Carrinho desativado que tenha horário na campanha também aparece
    extras = [
        db.session.get(Carrinho, cid)
        for cid in por_carrinho
        if cid not in {c.id for c in carrinhos}
    ]

    locais = (
        Local.query
        .filter_by(ativo=True)
        .order_by(Local.nome)
        .all()
    )

    return render_template(
        "admin_campanha.html",
        campanha=campanha,
        carrinhos=carrinhos + extras,
        por_carrinho=por_carrinho,
        locais=locais,
        dias=DIAS_SEMANA,
        dias_curtos=DIAS_CURTOS,
        hoje=hoje_sao_paulo(),
        reservas_fora=_reservas_fora_da_campanha(campanha)
    )


@routes.route("/admin/campanhas/<int:campanha_id>/editar", methods=["POST"])
@admin_required
def editar_campanha(campanha_id):

    campanha = Campanha.query.get_or_404(campanha_id)

    nome = request.form.get("nome", "").strip()
    data_texto = request.form.get("data", "")
    local_id = request.form.get("local_id", type=int) or campanha.local_id

    if not nome or not data_texto:
        return _voltar_campanha(campanha.id, "Informe o nome e a data da campanha.")

    if not local_id or not db.session.get(Local, local_id):
        return _voltar_campanha(campanha.id, "Escolha o local da campanha.")

    try:
        data = date.fromisoformat(data_texto)
    except ValueError:
        return _voltar_campanha(campanha.id, "Data inválida.")

    if data != campanha.data:

        if data < hoje_sao_paulo():
            return _voltar_campanha(campanha.id, "Escolha uma data de hoje em diante.")

        if Campanha.query.filter(Campanha.data == data, Campanha.id != campanha.id).first():
            return _voltar_campanha(campanha.id, "Já existe outra campanha nessa data.")

        # Não muda a data se já tem gente reservada nos horários da campanha
        if any(_reservas_do_horario_campanha(h) for h in campanha.horarios):
            return _voltar_campanha(
                campanha.id,
                "Não é possível mudar a data: já existem reservas nos "
                "horários desta campanha. Cancele as reservas primeiro."
            )

    campanha.nome = nome
    campanha.data = data

    # Todos os carrinhos ficam no local da campanha
    campanha.local_id = local_id

    for horario in campanha.horarios:
        horario.local_id = local_id

    db.session.commit()

    return _voltar_campanha(campanha.id, "Campanha atualizada.", "success")


@routes.route("/admin/campanhas/<int:campanha_id>/excluir", methods=["POST"])
@admin_required
def excluir_campanha(campanha_id):

    campanha = Campanha.query.get_or_404(campanha_id)

    if any(_reservas_do_horario_campanha(h) for h in campanha.horarios):
        return _voltar_campanha(
            campanha.id,
            "Não é possível excluir: já existem reservas nos horários "
            "desta campanha. Cancele as reservas primeiro.",
            "danger"
        )

    nome = campanha.nome

    db.session.delete(campanha)
    db.session.commit()

    return voltar_admin_com_flash(
        "routes.admin_campanhas",
        f"Campanha “{nome}” excluída. A programação normal do dia volta a valer.",
        "success"
    )


@routes.route("/admin/campanhas/<int:campanha_id>/copiar", methods=["POST"])
@admin_required
def copiar_programacao_campanha(campanha_id):

    campanha = Campanha.query.get_or_404(campanha_id)

    semanais = (
        ProgramacaoSemanal.query
        .filter_by(dia_semana=campanha.data.weekday())
        .order_by(ProgramacaoSemanal.carrinho_id, ProgramacaoSemanal.hora_inicio)
        .all()
    )

    copiados = 0

    for s in semanais:

        if not s.carrinho.ativo:
            continue

        # Pula se já tem um horário que se sobrepõe nesse carrinho
        sobrepoe = any(
            h.carrinho_id == s.carrinho_id
            and h.hora_inicio < s.hora_fim
            and h.hora_fim > s.hora_inicio
            for h in campanha.horarios
        )

        if sobrepoe:
            continue

        campanha.horarios.append(CampanhaHorario(
            carrinho_id=s.carrinho_id,
            local_id=campanha.local_id or s.local_id,
            hora_inicio=s.hora_inicio,
            hora_fim=s.hora_fim,
            quantidade_maxima=s.quantidade_maxima
        ))

        copiados += 1

    db.session.commit()

    dia = DIAS_SEMANA[campanha.data.weekday()].lower()

    if not copiados:
        return _voltar_campanha(
            campanha.id,
            f"Nada para copiar: a programação normal de {dia} já está na campanha."
        )

    return _voltar_campanha(
        campanha.id,
        f"{copiados} horário{'' if copiados == 1 else 's'} copiado{'' if copiados == 1 else 's'} "
        f"da programação normal de {dia}. Agora é só ajustar o que muda.",
        "success"
    )


def _validar_horario_campanha(campanha, form, ignorar_id=None):
    """Lê e confere os campos de um horário. Devolve (dados, erro)."""

    try:
        carrinho_id = int(form.get("carrinho_id", ""))
        # Todos os horários usam o local da campanha
        local_id = campanha.local_id or int(form.get("local_id", ""))
        inicio = _ler_hora(form.get("hora_inicio", ""))
        fim = _ler_hora(form.get("hora_fim", ""))
        vagas = int(form.get("quantidade_maxima", "") or 3)
    except ValueError:
        return None, "Preencha carrinho, início e fim."

    if vagas < 1:
        return None, "O número de vagas deve ser pelo menos 1."

    if inicio >= fim:
        return None, "O horário de início deve ser antes do horário de fim."

    if not db.session.get(Carrinho, carrinho_id):
        return None, "Carrinho não encontrado."

    if not db.session.get(Local, local_id):
        return None, "Local não encontrado."

    for h in campanha.horarios:
        if (
            h.id != ignorar_id
            and h.carrinho_id == carrinho_id
            and h.hora_inicio < fim
            and h.hora_fim > inicio
        ):
            return None, (
                "Esse carrinho já tem um horário que se sobrepõe na campanha "
                f"({h.hora_inicio.strftime('%H:%M')} às {h.hora_fim.strftime('%H:%M')})."
            )

    return dict(
        carrinho_id=carrinho_id,
        local_id=local_id,
        hora_inicio=inicio,
        hora_fim=fim,
        quantidade_maxima=vagas
    ), None


@routes.route("/admin/campanhas/<int:campanha_id>/horario/adicionar", methods=["POST"])
@admin_required
def adicionar_horario_campanha(campanha_id):

    campanha = Campanha.query.get_or_404(campanha_id)

    dados, erro = _validar_horario_campanha(campanha, request.form)

    if erro:
        return _voltar_campanha(campanha.id, erro)

    campanha.horarios.append(CampanhaHorario(**dados))
    db.session.commit()

    return _voltar_campanha(
        campanha.id,
        f"Horário adicionado: {dados['hora_inicio'].strftime('%H:%M')} às "
        f"{dados['hora_fim'].strftime('%H:%M')}.",
        "success"
    )


@routes.route("/admin/campanhas/horario/<int:horario_id>/editar", methods=["POST"])
@admin_required
def editar_horario_campanha(horario_id):

    horario = CampanhaHorario.query.get_or_404(horario_id)
    campanha = horario.campanha

    form = request.form.to_dict()
    form["carrinho_id"] = str(horario.carrinho_id)

    dados, erro = _validar_horario_campanha(campanha, form, ignorar_id=horario.id)

    if erro:
        return _voltar_campanha(campanha.id, erro)

    mudou_hora = (
        dados["hora_inicio"] != horario.hora_inicio
        or dados["hora_fim"] != horario.hora_fim
    )

    if mudou_hora and _reservas_do_horario_campanha(horario):
        return _voltar_campanha(
            campanha.id,
            "Este horário já tem reservas: dá para mudar o local e as vagas, "
            "mas não o horário. Cancele as reservas para mudar o horário."
        )

    horario.local_id = dados["local_id"]
    horario.hora_inicio = dados["hora_inicio"]
    horario.hora_fim = dados["hora_fim"]
    horario.quantidade_maxima = dados["quantidade_maxima"]

    db.session.commit()

    return _voltar_campanha(campanha.id, "Horário atualizado.", "success")


@routes.route("/admin/campanhas/horario/<int:horario_id>/remover", methods=["POST"])
@admin_required
def remover_horario_campanha(horario_id):

    horario = CampanhaHorario.query.get_or_404(horario_id)
    campanha = horario.campanha

    if _reservas_do_horario_campanha(horario):
        return _voltar_campanha(
            campanha.id,
            "Não é possível remover este horário porque ele tem reservas. "
            "Cancele as reservas primeiro.",
            "danger"
        )

    db.session.delete(horario)
    db.session.commit()

    return _voltar_campanha(campanha.id, "Horário removido.", "success")


# =========================================================
# ADMIN - AVISOS
# O admin manda um recado para o sininho dos irmãos
# =========================================================

def _usuarios_que_recebem():
    """Usuários com login ativo, com o nome da pessoa quando houver."""
    usuarios = Usuario.query.filter_by(ativo=True).all()

    nomes = {
        p.usuario_id: p.nome
        for p in Pessoa.query.filter(Pessoa.usuario_id.isnot(None)).all()
    }

    lista = [
        {"id": u.id, "nome": nomes.get(u.id, u.nome)}
        for u in usuarios
    ]

    return sorted(lista, key=lambda item: item["nome"].lower())


@routes.route("/admin/avisos")
@admin_required
def admin_avisos():

    # Agrupa os avisos já enviados (mesmo título, mensagem e minuto)
    enviados = {}

    avisos = (
        Notificacao.query
        .filter(Notificacao.titulo.startswith(PREFIXO_AVISO))
        .order_by(Notificacao.criado_em.desc())
        .all()
    )

    for n in avisos:
        remetente, titulo = ler_aviso(n.titulo) or ("", n.titulo)
        minuto = n.criado_em.strftime("%Y-%m-%d %H:%M") if n.criado_em else ""
        chave = (titulo, n.mensagem, minuto)

        if chave not in enviados:
            enviados[chave] = {
                "titulo": titulo,
                "mensagem": n.mensagem,
                "quando": n.criado_em.strftime("%d/%m %H:%M") if n.criado_em else "",
                "remetente": remetente,
                "pessoas": 0
            }

        enviados[chave]["pessoas"] += 1

    return render_template(
        "admin_avisos.html",
        usuarios=_usuarios_que_recebem(),
        enviados=list(enviados.values())[:20]
    )


@routes.route("/admin/avisos/enviar", methods=["POST"])
@admin_required
def enviar_aviso():

    destino = "routes.admin_avisos"

    titulo = request.form.get("titulo", "").strip()
    mensagem = request.form.get("mensagem", "").strip()
    para = request.form.get("para", "todos")

    if not titulo or not mensagem:
        return voltar_admin_com_flash(destino, "Escreva o título e a mensagem do aviso.")

    if len(titulo) > 100:
        return voltar_admin_com_flash(destino, "O título pode ter no máximo 100 letras.")

    if len(mensagem) > 500:
        return voltar_admin_com_flash(destino, "A mensagem pode ter no máximo 500 letras.")

    possiveis = {u["id"] for u in _usuarios_que_recebem()}

    if para == "escolher":
        escolhidos = {
            int(x) for x in request.form.getlist("usuarios")
            if x.isdigit()
        } & possiveis
    else:
        escolhidos = possiveis

    if not escolhidos:
        return voltar_admin_com_flash(destino, "Escolha pelo menos uma pessoa.")

    pessoa = Pessoa.query.filter_by(usuario_id=current_user.id).first()
    remetente = (pessoa.nome if pessoa else current_user.nome).replace("|", " ")

    agora = agora_sao_paulo().replace(tzinfo=None)

    for usuario_id in escolhidos:
        db.session.add(Notificacao(
            usuario_id=usuario_id,
            titulo=f"{PREFIXO_AVISO}{remetente}|{titulo}"[:150],
            mensagem=mensagem,
            lida=False,
            criado_em=agora
        ))

    db.session.commit()

    total = len(escolhidos)

    return voltar_admin_com_flash(
        destino,
        f"Aviso enviado para {total} pessoa{'' if total == 1 else 's'}.",
        "success"
    )
