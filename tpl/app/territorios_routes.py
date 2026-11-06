import csv
import io
import json
import re
import secrets
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from urllib.parse import quote

from flask import (
    Blueprint,
    Response,
    abort,
    render_template,
    request,
    redirect,
    url_for,
    flash
)

from flask_login import current_user

from app.models import (
    db,
    Pessoa,
    Notificacao,
    Territorio,
    QuadraTerritorio,
    DesignacaoTerritorio,
    DirigenteTerritorio,
    TrabalhoTerritorio,
    ConfigTerritorio
)

from app.routes import admin_required, hoje_sao_paulo


# Área administrativa
territorios = Blueprint(
    "territorios",
    __name__,
    url_prefix="/admin/territorios"
)

# Ficha que o dirigente abre pelo link (sem login)
ficha = Blueprint("ficha", __name__)


ETIQUETAS = ["Residencial", "Comercial", "Rural"]

STATUS_NOMES = {
    "disponivel": "Disponível",
    "em_campo": "Em campo",
    "vencido": "Prazo vencido"
}

PERCENTUAIS = [10, 20, 25, 30, 40, 50, 60, 70, 75, 80, 90, 100]

# "Cobertura completa" conta o que foi concluído nesse período
DIAS_COBERTURA_COMPLETA = 120

TAMANHO_MAXIMO_IMPORTACAO = 5 * 1024 * 1024  # 5 MB


# =========================================================
# AJUSTES GUARDADOS
# =========================================================

def config(chave, padrao=""):
    registro = db.session.get(ConfigTerritorio, chave)
    return registro.valor if registro and registro.valor is not None else padrao


def salvar_config(chave, valor):
    registro = db.session.get(ConfigTerritorio, chave)

    if registro:
        registro.valor = valor
    else:
        db.session.add(ConfigTerritorio(chave=chave, valor=valor))


def prazo_padrao_dias():
    try:
        return max(1, min(int(config("prazo_padrao_dias", "120")), 730))
    except ValueError:
        return 120


# =========================================================
# FUNÇÕES AUXILIARES
# =========================================================

def ler_data(texto, padrao=None):
    try:
        return date.fromisoformat((texto or "").strip())
    except ValueError:
        return padrao


def ler_percentual(texto, padrao=None):
    try:
        valor = int(texto)
    except (TypeError, ValueError):
        return padrao
    return max(1, min(valor, 100))


def so_numeros(telefone):
    numeros = re.sub(r"\D", "", telefone or "")

    # Tira o 55 do Brasil se vier junto
    if len(numeros) in (12, 13) and numeros.startswith("55"):
        numeros = numeros[2:]

    return numeros[:11] or None


def link_whatsapp(numero, texto):
    destino = f"55{numero}" if numero else ""
    return f"https://wa.me/{destino}?text={quote(texto)}"


def inicio_da_semana(dia):
    return dia - timedelta(days=dia.weekday())


def ler_area(texto):
    """
    Valida o JSON da área vindo do formulário.
    Aceita uma lista de polígonos, cada um com 3 ou mais
    pontos [lat, lng]. Devolve o JSON limpo ou None.
    """
    try:
        dados = json.loads(texto or "null")
    except ValueError:
        return None

    if not isinstance(dados, list):
        return None

    poligonos = []

    for poligono in dados:
        if not isinstance(poligono, list):
            continue

        pontos = []

        for ponto in poligono:
            try:
                lat, lng = float(ponto[0]), float(ponto[1])
            except (TypeError, ValueError, IndexError):
                continue

            if -90 <= lat <= 90 and -180 <= lng <= 180:
                pontos.append([round(lat, 7), round(lng, 7)])

        if len(pontos) >= 3:
            poligonos.append(pontos)

    return json.dumps(poligonos) if poligonos else None


def centro_area(area_json):
    try:
        poligonos = json.loads(area_json or "[]")
        pontos = [p for poligono in poligonos for p in poligono]
    except ValueError:
        return None

    if not pontos:
        return None

    return (
        sum(p[0] for p in pontos) / len(pontos),
        sum(p[1] for p in pontos) / len(pontos)
    )


def link_rota(territorio):
    centro = centro_area(territorio.area)

    if not centro:
        return None

    return (
        "https://www.google.com/maps/dir/?api=1&destination="
        f"{centro[0]:.6f},{centro[1]:.6f}"
    )


def dentro_do_poligono(lat, lng, poligono):
    """Teste do raio: o ponto está dentro do polígono?"""
    dentro = False
    j = len(poligono) - 1

    for i in range(len(poligono)):
        yi, xi = poligono[i]
        yj, xj = poligono[j]

        if (yi > lat) != (yj > lat):
            x_corte = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lng < x_corte:
                dentro = not dentro

        j = i

    return dentro


def dados_mapa(lista, hoje):
    """Territórios no formato que o mapa (JavaScript) usa."""
    resultado = []

    for territorio in lista:
        if not territorio.area:
            continue

        resultado.append({
            "id": territorio.id,
            "numero": territorio.numero,
            "nome": territorio.nome,
            "status": territorio.status(hoje),
            "area": json.loads(territorio.area),
            "url": url_for("territorios.detalhe", territorio_id=territorio.id)
        })

    return resultado


def dirigentes_disponiveis():
    """
    Pessoas que aparecem na hora de designar.
    Se nenhum dirigente foi cadastrado ainda, mostra todas.
    Devolve (lista de pessoas, configurado, {pessoa_id: whatsapp}).
    """
    cadastrados = DirigenteTerritorio.query.all()
    whatsapps = {d.pessoa_id: d.whatsapp for d in cadastrados}

    consulta = Pessoa.query.filter_by(ativo=True)

    if cadastrados:
        consulta = consulta.filter(Pessoa.id.in_(list(whatsapps)))

    return consulta.order_by(Pessoa.nome).all(), bool(cadastrados), whatsapps


def territorios_ativos():
    return (
        Territorio.query
        .filter_by(ativo=True)
        .order_by(Territorio.numero)
        .all()
    )


def proximo_numero():
    ultimo = db.session.query(db.func.max(Territorio.numero)).scalar()
    return (ultimo or 0) + 1


def garantir_codigo(designacao):
    if not designacao.codigo:
        designacao.codigo = secrets.token_urlsafe(12)
    return designacao.codigo


def link_ficha(codigo):
    """Endereço completo da ficha, para mandar pelo WhatsApp."""
    https = (
        request.is_secure
        or request.headers.get("X-Forwarded-Proto", "") == "https"
        or not request.host.startswith(("localhost", "127.0.0.1"))
    )
    esquema = "https" if https else "http"
    return f"{esquema}://{request.host}{url_for('ficha.ver', codigo=codigo)}"


def mensagem_whatsapp(designacao):
    territorio = designacao.territorio
    primeiro_nome = designacao.dirigente.split()[0] if designacao.dirigente != "—" else ""
    prazo = designacao.prazo.strftime("%d/%m/%Y") if designacao.prazo else "—"

    linhas = [
        f"Olá{', ' + primeiro_nome if primeiro_nome else ''}! 👋",
        "",
        f"Você recebeu o *Território {territorio.numero} — {territorio.nome}*.",
        f"📅 Prazo para devolver: {prazo}",
        f"🗺️ Mapa, ruas e quadras: {link_ficha(garantir_codigo(designacao))}",
    ]

    if designacao.observacao:
        linhas.append(f"📝 {designacao.observacao}")

    assinatura = config("assinatura").strip()

    if assinatura:
        linhas += ["", assinatura]

    return "\n".join(linhas)


# =========================================================
# COBERTURA (quanto foi trabalhado em cada semana)
# =========================================================

def classe_percentual(percentual):
    if percentual >= 100:
        return "completo"
    if percentual >= 50:
        return "metade"
    return "parcial"


def grade_cobertura(lista, semanas):
    """
    Para cada território, uma célula por semana:
    {"classe": "", "percentual": None}
    classe: "" (nada), "parcial", "metade", "completo" ou
    "campo" (estava em campo, mas sem registro).
    """
    if not lista or not semanas:
        return {}

    inicio = semanas[0]
    fim = semanas[-1] + timedelta(days=6)
    ids = [t.id for t in lista]

    trabalhos = TrabalhoTerritorio.query.filter(
        TrabalhoTerritorio.territorio_id.in_(ids),
        TrabalhoTerritorio.data >= inicio,
        TrabalhoTerritorio.data <= fim
    ).all()

    maior = {}

    for trabalho in trabalhos:
        chave = (trabalho.territorio_id, inicio_da_semana(trabalho.data))
        maior[chave] = max(maior.get(chave, 0), trabalho.percentual)

    grade = {}

    for territorio in lista:
        celulas = []

        for semana in semanas:
            fim_semana = semana + timedelta(days=6)
            percentual = maior.get((territorio.id, semana))

            if percentual is not None:
                celulas.append({"classe": classe_percentual(percentual), "percentual": percentual})
                continue

            em_campo = any(
                d.data_saida <= fim_semana
                and (d.data_devolucao is None or d.data_devolucao >= semana)
                for d in territorio.designacoes
            )

            celulas.append({"classe": "campo" if em_campo else "", "percentual": None})

        grade[territorio.id] = celulas

    return grade


def ultima_completa(territorio_ids):
    """{territorio_id: data da última cobertura completa}"""
    if not territorio_ids:
        return {}

    linhas = (
        db.session.query(
            TrabalhoTerritorio.territorio_id,
            db.func.max(TrabalhoTerritorio.data)
        )
        .filter(
            TrabalhoTerritorio.territorio_id.in_(territorio_ids),
            TrabalhoTerritorio.percentual >= 100
        )
        .group_by(TrabalhoTerritorio.territorio_id)
        .all()
    )

    return dict(linhas)


def ultimo_trabalho(territorio_ids):
    """{territorio_id: TrabalhoTerritorio mais recente}"""
    resultado = {}

    if not territorio_ids:
        return resultado

    for trabalho in (
        TrabalhoTerritorio.query
        .filter(TrabalhoTerritorio.territorio_id.in_(territorio_ids))
        .order_by(TrabalhoTerritorio.data, TrabalhoTerritorio.id)
        .all()
    ):
        resultado[trabalho.territorio_id] = trabalho

    return resultado


def ha_quanto_tempo(data, hoje):
    dias = (hoje - data).days

    if dias <= 0:
        return "hoje"
    if dias == 1:
        return "ontem"
    if dias < 30:
        return f"há {dias} dias"
    if dias < 365:
        meses = dias // 30
        return f"há {meses} {'mês' if meses == 1 else 'meses'}"

    anos = dias // 365
    return f"há {anos} ano{'s' if anos > 1 else ''}"


def resumo_trabalho(territorio, ultimo, hoje):
    atual = territorio.designacao_atual

    if atual:
        texto = f"{atual.dirigente} · até {atual.prazo.strftime('%d/%m') if atual.prazo else '—'}"
        if atual.cobertura:
            texto += f" · {atual.cobertura}% feito"
        return texto

    if ultimo:
        return (
            f"{ultimo.percentual}% em {ultimo.data.strftime('%d/%m/%Y')} "
            f"({ha_quanto_tempo(ultimo.data, hoje)})"
        )

    return "Nenhum trabalho registrado"


def registrar_trabalho(designacao, data, percentual, observacao=None):
    db.session.add(TrabalhoTerritorio(
        designacao_id=designacao.id,
        territorio_id=designacao.territorio_id,
        data=data,
        percentual=percentual,
        observacao=(observacao or "").strip()[:300] or None
    ))


# =========================================================
# PAINEL
# =========================================================

@territorios.route("/")
@admin_required
def painel():
    hoje = hoje_sao_paulo()
    lista = territorios_ativos()
    ids = [t.id for t in lista]

    contagem = {"disponivel": 0, "em_campo": 0, "vencido": 0}

    for territorio in lista:
        contagem[territorio.status(hoje)] += 1

    envios_pendentes = sum(
        1 for t in lista
        if t.designacao_atual and not t.designacao_atual.mapa_enviado_em
    )

    semana_atual = inicio_da_semana(hoje)

    trabalhados_semana = db.session.query(
        db.func.count(db.distinct(TrabalhoTerritorio.territorio_id))
    ).filter(
        TrabalhoTerritorio.territorio_id.in_(ids),
        TrabalhoTerritorio.data >= semana_atual,
        TrabalhoTerritorio.data <= semana_atual + timedelta(days=6)
    ).scalar() if ids else 0

    completas = ultima_completa(ids)
    limite_completa = hoje - timedelta(days=DIAS_COBERTURA_COMPLETA)
    cobertura_completa = sum(1 for d in completas.values() if d >= limite_completa)

    # Precisa de atenção
    vencidos = [t for t in lista if t.status(hoje) == "vencido"]

    sugestoes = sorted(
        [t for t in lista if t.status(hoje) == "disponivel"],
        key=lambda t: (completas.get(t.id) is not None, completas.get(t.id) or date.min, t.numero)
    )[:4]

    semanas = [semana_atual - timedelta(weeks=i) for i in range(7, -1, -1)]

    ultimos = ultimo_trabalho(ids)

    return render_template(
        "territorios_painel.html",
        aba="painel",
        territorios=lista,
        contagem=contagem,
        envios_pendentes=envios_pendentes,
        trabalhados_semana=trabalhados_semana,
        semana_atual=semana_atual,
        semana_fim=semana_atual + timedelta(days=6),
        cobertura_completa=cobertura_completa,
        meses_completa=DIAS_COBERTURA_COMPLETA // 30,
        vencidos=vencidos,
        sugestoes=sugestoes,
        completas=completas,
        grade=grade_cobertura(lista, semanas),
        resumos={t.id: resumo_trabalho(t, ultimos.get(t.id), hoje) for t in lista},
        mapa=dados_mapa(lista, hoje),
        hoje=hoje,
        status_nomes=STATUS_NOMES
    )


# =========================================================
# TERRITÓRIOS (lista com ruas)
# =========================================================

@territorios.route("/lista")
@admin_required
def lista():
    hoje = hoje_sao_paulo()
    todos = (
        Territorio.query
        .order_by(Territorio.ativo.desc(), Territorio.numero)
        .all()
    )
    ultimos = ultimo_trabalho([t.id for t in todos])

    return render_template(
        "territorios_lista.html",
        aba="lista",
        territorios=todos,
        resumos={t.id: resumo_trabalho(t, ultimos.get(t.id), hoje) for t in todos},
        mapa=dados_mapa([t for t in todos if t.ativo], hoje),
        hoje=hoje,
        status_nomes=STATUS_NOMES
    )


# =========================================================
# DETALHE
# =========================================================

@territorios.route("/<int:territorio_id>")
@admin_required
def detalhe(territorio_id):
    territorio = Territorio.query.get_or_404(territorio_id)
    hoje = hoje_sao_paulo()

    pessoas, dirigentes_configurados, whatsapps = dirigentes_disponiveis()

    atual = territorio.designacao_atual
    whatsapp_atual = None

    if atual:
        garantir_codigo(atual)
        db.session.commit()

        if atual.pessoa_id:
            dirigente = db.session.get(DirigenteTerritorio, atual.pessoa_id)
            whatsapp_atual = dirigente.whatsapp if dirigente else None

    semanas = [inicio_da_semana(hoje) - timedelta(weeks=i) for i in range(11, -1, -1)]

    return render_template(
        "territorio_detalhe.html",
        territorio=territorio,
        atual=atual,
        whatsapp_atual=whatsapp_atual,
        link_da_ficha=link_ficha(atual.codigo) if atual else None,
        status=territorio.status(hoje),
        status_nomes=STATUS_NOMES,
        area=json.loads(territorio.area) if territorio.area else [],
        quadras_mapa=[
            {"numero": q.numero, "lat": q.lat, "lng": q.lng}
            for q in territorio.quadras
            if q.lat is not None and q.lng is not None
        ],
        rota=link_rota(territorio),
        pessoas=pessoas,
        whatsapps=whatsapps,
        dirigentes_configurados=dirigentes_configurados,
        celulas=grade_cobertura([territorio], semanas).get(territorio.id, []),
        semanas=semanas,
        percentuais=PERCENTUAIS,
        hoje=hoje,
        prazo_padrao=hoje + timedelta(days=prazo_padrao_dias())
    )


# =========================================================
# CADASTRO E EDIÇÃO
# =========================================================

def outros_para_mapa(territorio_id=None):
    """Áreas dos outros territórios, em cinza, para referência."""
    hoje = hoje_sao_paulo()
    consulta = Territorio.query.filter(Territorio.area.isnot(None))

    if territorio_id:
        consulta = consulta.filter(Territorio.id != territorio_id)

    return dados_mapa(consulta.all(), hoje)


def salvar_quadras(territorio):
    """Lê as quadras do formulário e substitui as atuais."""
    numeros = request.form.getlist("quadra_numero")
    descricoes = request.form.getlist("quadra_descricao")
    links = request.form.getlist("quadra_link")
    lats = request.form.getlist("quadra_lat")
    lngs = request.form.getlist("quadra_lng")

    territorio.quadras.clear()

    for i, numero in enumerate(numeros):
        numero = numero.strip()

        if not numero:
            continue

        def campo(valores):
            return valores[i].strip() if i < len(valores) else ""

        def coordenada(valores):
            try:
                return float(campo(valores))
            except ValueError:
                return None

        link = campo(links)

        if link and not link.startswith(("http://", "https://")):
            link = "https://" + link

        territorio.quadras.append(QuadraTerritorio(
            ordem=i,
            numero=numero[:20],
            descricao=campo(descricoes)[:200] or None,
            link=link[:500] or None,
            lat=coordenada(lats),
            lng=coordenada(lngs)
        ))


@territorios.route("/novo", methods=["GET", "POST"])
@admin_required
def novo():
    if request.method == "POST":
        return salvar(Territorio())

    return render_template(
        "territorio_form.html",
        aba="lista",
        territorio=None,
        numero=proximo_numero(),
        etiquetas=ETIQUETAS,
        outros=outros_para_mapa()
    )


@territorios.route("/<int:territorio_id>/editar", methods=["GET", "POST"])
@admin_required
def editar(territorio_id):
    territorio = Territorio.query.get_or_404(territorio_id)

    if request.method == "POST":
        return salvar(territorio)

    return render_template(
        "territorio_form.html",
        aba="lista",
        territorio=territorio,
        numero=territorio.numero,
        etiquetas=ETIQUETAS,
        outros=outros_para_mapa(territorio.id)
    )


def salvar(territorio):
    novo_cadastro = territorio.id is None
    voltar = (
        url_for("territorios.novo") if novo_cadastro
        else url_for("territorios.editar", territorio_id=territorio.id)
    )

    nome = request.form.get("nome", "").strip()

    try:
        numero = int(request.form.get("numero", ""))
    except ValueError:
        numero = None

    if not nome or not numero or numero < 1:
        flash("Preencha o número e o nome do território.", "warning")
        return redirect(voltar)

    repetido = Territorio.query.filter(
        Territorio.numero == numero,
        Territorio.id != territorio.id
    ).first()

    if repetido:
        flash(f"Já existe o território {numero} ({repetido.nome}).", "warning")
        return redirect(voltar)

    etiqueta = request.form.get("etiqueta", "")

    territorio.numero = numero
    territorio.nome = nome[:150]
    territorio.etiqueta = etiqueta if etiqueta in ETIQUETAS else None
    territorio.observacoes = request.form.get("observacoes", "").strip() or None
    territorio.ruas = "\n".join(
        r.strip() for r in request.form.get("ruas", "").splitlines() if r.strip()
    ) or None
    territorio.area = ler_area(request.form.get("area"))
    territorio.ativo = request.form.get("ativo", "1") == "1"

    salvar_quadras(territorio)

    if novo_cadastro:
        db.session.add(territorio)

    db.session.commit()

    flash(
        "Território cadastrado!" if novo_cadastro else "Território atualizado!",
        "success"
    )
    return redirect(url_for("territorios.detalhe", territorio_id=territorio.id))


@territorios.route("/<int:territorio_id>/excluir", methods=["POST"])
@admin_required
def excluir(territorio_id):
    territorio = Territorio.query.get_or_404(territorio_id)

    TrabalhoTerritorio.query.filter_by(territorio_id=territorio.id).delete(
        synchronize_session=False
    )
    db.session.delete(territorio)
    db.session.commit()

    flash(f"Território {territorio.numero} excluído.", "success")
    return redirect(url_for("territorios.lista"))


# =========================================================
# DESIGNAÇÕES
# =========================================================

def voltar_detalhe(territorio_id, ancora=""):
    return redirect(
        url_for("territorios.detalhe", territorio_id=territorio_id) + ancora
    )


@territorios.route("/<int:territorio_id>/designar", methods=["POST"])
@admin_required
def designar(territorio_id):
    territorio = Territorio.query.get_or_404(territorio_id)
    hoje = hoje_sao_paulo()

    if territorio.designacao_atual:
        flash("Este território já está em campo. Registre a devolução primeiro.", "warning")
        return voltar_detalhe(territorio.id)

    try:
        pessoa = db.session.get(Pessoa, int(request.form.get("pessoa_id", "")))
    except ValueError:
        pessoa = None

    if not pessoa:
        flash("Escolha o dirigente.", "warning")
        return voltar_detalhe(territorio.id, "#designacao")

    saida = ler_data(request.form.get("data_saida"), hoje)
    prazo = ler_data(
        request.form.get("prazo"),
        saida + timedelta(days=prazo_padrao_dias())
    )

    if prazo < saida:
        flash("O prazo não pode ser antes da data de saída.", "warning")
        return voltar_detalhe(territorio.id, "#designacao")

    designacao = DesignacaoTerritorio(
        territorio_id=territorio.id,
        pessoa_id=pessoa.id,
        dirigente_nome=pessoa.nome,
        data_saida=saida,
        prazo=prazo,
        observacao=request.form.get("observacao", "").strip()[:300] or None,
        criado_por_id=current_user.id
    )
    garantir_codigo(designacao)
    db.session.add(designacao)

    # Aviso no sininho de quem tem login no app
    avisou = False

    if request.form.get("avisar_sininho") == "1" and pessoa.usuario_id:
        db.session.add(Notificacao(
            usuario_id=pessoa.usuario_id,
            titulo=f"Território {territorio.numero} — {territorio.nome}"[:150],
            mensagem=(
                f"Você recebeu este território. Prazo para devolver: "
                f"{prazo.strftime('%d/%m/%Y')}. "
                f"{url_for('ficha.ver', codigo=designacao.codigo)}"
            )[:500],
            lida=False
        ))
        avisou = True

    db.session.commit()

    flash(
        f"Território {territorio.numero} designado para {pessoa.nome}."
        + (" Aviso enviado no sininho." if avisou else "")
        + " Agora envie o mapa pelo WhatsApp.",
        "success"
    )
    return voltar_detalhe(territorio.id, "#designacao")


@territorios.route("/designacao/<int:designacao_id>/whatsapp")
@admin_required
def enviar_whatsapp(designacao_id):
    designacao = DesignacaoTerritorio.query.get_or_404(designacao_id)

    numero = None

    if designacao.pessoa_id:
        dirigente = db.session.get(DirigenteTerritorio, designacao.pessoa_id)
        numero = dirigente.whatsapp if dirigente else None

    texto = mensagem_whatsapp(designacao)

    if not designacao.mapa_enviado_em:
        designacao.mapa_enviado_em = datetime.utcnow()

    db.session.commit()

    return redirect(link_whatsapp(numero, texto))


@territorios.route("/<int:territorio_id>/trabalho", methods=["POST"])
@admin_required
def trabalho(territorio_id):
    territorio = Territorio.query.get_or_404(territorio_id)
    atual = territorio.designacao_atual

    if not atual:
        flash("Designe o território antes de registrar o trabalho.", "warning")
        return voltar_detalhe(territorio.id)

    data = ler_data(request.form.get("data"), hoje_sao_paulo())
    percentual = ler_percentual(request.form.get("percentual"))

    if not percentual:
        flash("Escolha quanto foi trabalhado.", "warning")
        return voltar_detalhe(territorio.id, "#designacao")

    if data < atual.data_saida:
        flash("A data não pode ser antes da saída do território.", "warning")
        return voltar_detalhe(territorio.id, "#designacao")

    registrar_trabalho(atual, data, percentual, request.form.get("observacao"))
    db.session.commit()

    flash(f"Registrado: {percentual}% trabalhado.", "success")
    return voltar_detalhe(territorio.id, "#designacao")


@territorios.route("/trabalho/<int:trabalho_id>/excluir", methods=["POST"])
@admin_required
def excluir_trabalho(trabalho_id):
    registro = TrabalhoTerritorio.query.get_or_404(trabalho_id)
    territorio_id = registro.territorio_id

    db.session.delete(registro)
    db.session.commit()

    flash("Registro de trabalho removido.", "success")
    return voltar_detalhe(territorio_id, "#designacao")


@territorios.route("/<int:territorio_id>/devolver", methods=["POST"])
@admin_required
def devolver(territorio_id):
    territorio = Territorio.query.get_or_404(territorio_id)
    atual = territorio.designacao_atual

    if not atual:
        flash("Este território não está em campo.", "warning")
        return voltar_detalhe(territorio.id)

    devolucao = ler_data(request.form.get("data_devolucao"), hoje_sao_paulo())

    if devolucao < atual.data_saida:
        flash("A devolução não pode ser antes da data de saída.", "warning")
        return voltar_detalhe(territorio.id, "#designacao")

    percentual = ler_percentual(request.form.get("percentual"), 100)

    # Registra a cobertura final, se for maior que a já anotada
    if percentual > atual.cobertura:
        registrar_trabalho(atual, devolucao, percentual)

    atual.data_devolucao = devolucao
    db.session.commit()

    flash(f"Território {territorio.numero} devolvido ({max(percentual, atual.cobertura)}% trabalhado).", "success")
    return voltar_detalhe(territorio.id)


@territorios.route("/<int:territorio_id>/prazo", methods=["POST"])
@admin_required
def alterar_prazo(territorio_id):
    territorio = Territorio.query.get_or_404(territorio_id)
    atual = territorio.designacao_atual
    prazo = ler_data(request.form.get("prazo"))

    if not atual or not prazo:
        flash("Informe o novo prazo.", "warning")
        return voltar_detalhe(territorio.id, "#designacao")

    if prazo < atual.data_saida:
        flash("O prazo não pode ser antes da data de saída.", "warning")
        return voltar_detalhe(territorio.id, "#designacao")

    atual.prazo = prazo
    db.session.commit()

    flash("Prazo atualizado.", "success")
    return voltar_detalhe(territorio.id, "#designacao")


@territorios.route("/designacao/<int:designacao_id>/excluir", methods=["POST"])
@admin_required
def excluir_designacao(designacao_id):
    designacao = DesignacaoTerritorio.query.get_or_404(designacao_id)
    territorio_id = designacao.territorio_id

    db.session.delete(designacao)
    db.session.commit()

    flash("Registro removido do histórico.", "success")
    return voltar_detalhe(territorio_id, "#historico")


# =========================================================
# COBERTURA SEMANAL
# =========================================================

@territorios.route("/cobertura")
@admin_required
def cobertura():
    hoje = hoje_sao_paulo()
    semana_atual = inicio_da_semana(hoje)

    ultima = inicio_da_semana(ler_data(request.args.get("ate"), semana_atual))
    semanas = [ultima - timedelta(weeks=i) for i in range(11, -1, -1)]

    escolhida = inicio_da_semana(ler_data(request.args.get("semana"), ultima))

    lista = territorios_ativos()
    ids = [t.id for t in lista]
    grade = grade_cobertura(lista, semanas)

    # Gráfico: quantos territórios tiveram trabalho em cada semana
    por_semana = []

    for i, semana in enumerate(semanas):
        total = sum(
            1 for t in lista
            if grade.get(t.id) and grade[t.id][i]["percentual"] is not None
        )
        por_semana.append(total)

    # Trabalhos da semana escolhida
    trabalhos_semana = (
        TrabalhoTerritorio.query
        .filter(
            TrabalhoTerritorio.territorio_id.in_(ids),
            TrabalhoTerritorio.data >= escolhida,
            TrabalhoTerritorio.data <= escolhida + timedelta(days=6)
        )
        .order_by(TrabalhoTerritorio.data, TrabalhoTerritorio.id)
        .all()
    ) if ids else []

    # Um resumo por território (o maior % da semana)
    resumo = {}

    for registro in trabalhos_semana:
        anterior = resumo.get(registro.territorio_id)
        if not anterior or registro.percentual >= anterior.percentual:
            resumo[registro.territorio_id] = registro

    territorio_por_id = {t.id: t for t in lista}
    linhas_resumo = sorted(
        resumo.values(),
        key=lambda r: territorio_por_id[r.territorio_id].numero
    )

    fim_escolhida = escolhida + timedelta(days=6)
    texto = [f"*Cobertura da semana {escolhida.strftime('%d/%m')} a {fim_escolhida.strftime('%d/%m')}*", ""]

    if linhas_resumo:
        for registro in linhas_resumo:
            t = territorio_por_id[registro.territorio_id]
            situacao = "concluído ✅" if registro.percentual >= 100 else f"{registro.percentual}%"
            texto.append(f"• Território {t.numero} ({t.nome}): {situacao} — {registro.designacao.dirigente}")
    else:
        texto.append("Nenhum trabalho registrado nesta semana.")

    assinatura = config("assinatura").strip()
    if assinatura:
        texto += ["", assinatura]

    texto_resumo = "\n".join(texto)

    completas = ultima_completa(ids)
    mais_tempo = sorted(
        lista,
        key=lambda t: (completas.get(t.id) is not None, completas.get(t.id) or date.min, t.numero)
    )[:6]

    return render_template(
        "territorios_cobertura.html",
        aba="cobertura",
        territorios=lista,
        semanas=semanas,
        semana_atual=semana_atual,
        periodo_fim=ultima + timedelta(days=6),
        escolhida=escolhida,
        escolhida_fim=fim_escolhida,
        anterior=ultima - timedelta(weeks=12),
        seguinte=min(ultima + timedelta(weeks=12), semana_atual),
        pode_avancar=ultima < semana_atual,
        grade=grade,
        por_semana=por_semana,
        maior_semana=max(por_semana + [1]),
        linhas_resumo=linhas_resumo,
        territorio_por_id=territorio_por_id,
        texto_resumo=texto_resumo,
        whatsapp_resumo=link_whatsapp(None, texto_resumo),
        completas=completas,
        mais_tempo=mais_tempo,
        hoje=hoje,
        status_nomes=STATUS_NOMES
    )


# =========================================================
# DIRIGENTES
# =========================================================

@territorios.route("/dirigentes")
@admin_required
def dirigentes():
    hoje = hoje_sao_paulo()

    cadastrados = (
        DirigenteTerritorio.query
        .join(Pessoa)
        .order_by(Pessoa.nome)
        .all()
    )

    em_maos = {}
    total_historico = {}

    for designacao in DesignacaoTerritorio.query.all():
        if not designacao.pessoa_id:
            continue

        total_historico[designacao.pessoa_id] = total_historico.get(designacao.pessoa_id, 0) + 1

        if designacao.data_devolucao is None:
            em_maos.setdefault(designacao.pessoa_id, []).append(designacao)

    ja_dirigentes = {d.pessoa_id for d in cadastrados}

    outras_pessoas = (
        Pessoa.query
        .filter_by(ativo=True)
        .order_by(Pessoa.nome)
        .all()
    )
    outras_pessoas = [p for p in outras_pessoas if p.id not in ja_dirigentes]

    return render_template(
        "territorios_dirigentes.html",
        aba="dirigentes",
        dirigentes=cadastrados,
        em_maos=em_maos,
        total_historico=total_historico,
        outras_pessoas=outras_pessoas,
        hoje=hoje
    )


@territorios.route("/dirigentes/adicionar", methods=["POST"])
@admin_required
def adicionar_dirigentes():
    ids = set()

    for valor in request.form.getlist("pessoa_id"):
        try:
            ids.add(int(valor))
        except ValueError:
            pass

    if not ids:
        flash("Escolha pelo menos uma pessoa.", "warning")
        return redirect(url_for("territorios.dirigentes"))

    whatsapp = so_numeros(request.form.get("whatsapp")) if len(ids) == 1 else None
    adicionados = 0

    for pessoa in Pessoa.query.filter(Pessoa.id.in_(ids)).all():
        if db.session.get(DirigenteTerritorio, pessoa.id):
            continue

        db.session.add(DirigenteTerritorio(pessoa_id=pessoa.id, whatsapp=whatsapp))
        adicionados += 1

    db.session.commit()

    flash(
        f"{adicionados} dirigente{'s' if adicionados != 1 else ''} "
        f"adicionado{'s' if adicionados != 1 else ''}.",
        "success"
    )
    return redirect(url_for("territorios.dirigentes"))


@territorios.route("/dirigentes/<int:pessoa_id>/editar", methods=["POST"])
@admin_required
def editar_dirigente(pessoa_id):
    dirigente = DirigenteTerritorio.query.get_or_404(pessoa_id)

    numero = so_numeros(request.form.get("whatsapp"))

    if numero and len(numero) < 10:
        flash("WhatsApp inválido. Use DDD + número, ex.: (19) 99999-8888.", "warning")
        return redirect(url_for("territorios.dirigentes"))

    dirigente.whatsapp = numero
    db.session.commit()

    flash(f"{dirigente.pessoa.nome} atualizado.", "success")
    return redirect(url_for("territorios.dirigentes"))


@territorios.route("/dirigentes/<int:pessoa_id>/remover", methods=["POST"])
@admin_required
def remover_dirigente(pessoa_id):
    dirigente = DirigenteTerritorio.query.get_or_404(pessoa_id)
    nome = dirigente.pessoa.nome

    db.session.delete(dirigente)
    db.session.commit()

    flash(f"{nome} não aparece mais como dirigente. O histórico continua.", "success")
    return redirect(url_for("territorios.dirigentes"))


# =========================================================
# GESTÃO (página da Administração que junta Cobertura,
# Histórico e Ajustes)
# =========================================================

@territorios.route("/gestao")
@admin_required
def gestao():
    hoje = hoje_sao_paulo()

    return render_template(
        "territorios_gestao.html",
        aba="gestao",
        total_territorios=Territorio.query.filter_by(ativo=True).count(),
        total_designacoes=DesignacaoTerritorio.query.count(),
        em_campo=DesignacaoTerritorio.query.filter(
            DesignacaoTerritorio.data_devolucao.is_(None)
        ).count(),
        prazo_padrao_dias=prazo_padrao_dias(),
        hoje=hoje
    )


# =========================================================
# HISTÓRICO
# =========================================================

def filtrar_historico():
    hoje = hoje_sao_paulo()

    consulta = DesignacaoTerritorio.query.join(Territorio)

    try:
        territorio_id = int(request.args.get("territorio", ""))
        consulta = consulta.filter(DesignacaoTerritorio.territorio_id == territorio_id)
    except ValueError:
        territorio_id = None

    situacao = request.args.get("situacao", "")

    if situacao == "devolvido":
        consulta = consulta.filter(DesignacaoTerritorio.data_devolucao.isnot(None))
    elif situacao == "em_campo":
        consulta = consulta.filter(
            DesignacaoTerritorio.data_devolucao.is_(None),
            db.or_(DesignacaoTerritorio.prazo.is_(None), DesignacaoTerritorio.prazo >= hoje)
        )
    elif situacao == "vencido":
        consulta = consulta.filter(
            DesignacaoTerritorio.data_devolucao.is_(None),
            DesignacaoTerritorio.prazo < hoje
        )
    else:
        situacao = ""

    designacoes = (
        consulta
        .order_by(DesignacaoTerritorio.data_saida.desc(), DesignacaoTerritorio.id.desc())
        .limit(1000)
        .all()
    )

    return designacoes, territorio_id, situacao, hoje


def situacao_designacao(designacao, hoje):
    if designacao.data_devolucao:
        return "devolvido"
    if designacao.prazo and designacao.prazo < hoje:
        return "vencido"
    return "em_campo"


@territorios.route("/historico")
@admin_required
def historico():
    designacoes, territorio_id, situacao, hoje = filtrar_historico()

    return render_template(
        "territorios_historico.html",
        aba="historico",
        designacoes=designacoes,
        todos=Territorio.query.order_by(Territorio.numero).all(),
        territorio_id=territorio_id,
        situacao=situacao,
        situacoes={d.id: situacao_designacao(d, hoje) for d in designacoes},
        hoje=hoje
    )


@territorios.route("/historico.csv")
@admin_required
def historico_csv():
    designacoes, _, _, hoje = filtrar_historico()

    nomes = {"devolvido": "Devolvido", "em_campo": "Em campo", "vencido": "Prazo vencido"}

    def data(d):
        return d.strftime("%d/%m/%Y") if d else ""

    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";")
    escritor.writerow([
        "Território", "Nome", "Dirigente", "Entregue", "Prazo",
        "Mapa enviado", "Devolvido", "Cobertura (%)", "Situação"
    ])

    for d in designacoes:
        escritor.writerow([
            d.territorio.numero,
            d.territorio.nome,
            d.dirigente,
            data(d.data_saida),
            data(d.prazo),
            data(d.mapa_enviado_em),
            data(d.data_devolucao),
            d.cobertura or "",
            nomes[situacao_designacao(d, hoje)]
        ])

    # BOM para o Excel abrir os acentos certinho
    return Response(
        "﻿" + saida.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                f"attachment; filename=historico-territorios-{hoje.isoformat()}.csv"
        }
    )


# =========================================================
# AJUSTES E BACKUP
# =========================================================

@territorios.route("/ajustes", methods=["GET", "POST"])
@admin_required
def ajustes():
    if request.method == "POST":
        try:
            dias = int(request.form.get("prazo_padrao_dias", ""))
        except ValueError:
            dias = 0

        if not 1 <= dias <= 730:
            flash("O prazo padrão precisa ser entre 1 e 730 dias.", "warning")
            return redirect(url_for("territorios.ajustes"))

        salvar_config("prazo_padrao_dias", str(dias))
        salvar_config("assinatura", request.form.get("assinatura", "").strip()[:300])
        db.session.commit()

        flash("Ajustes salvos.", "success")
        return redirect(url_for("territorios.ajustes"))

    return render_template(
        "territorios_ajustes.html",
        aba="ajustes",
        prazo_padrao_dias=prazo_padrao_dias(),
        assinatura=config("assinatura"),
        total_designacoes=DesignacaoTerritorio.query.count()
    )


@territorios.route("/backup.json")
@admin_required
def backup():
    def data(d):
        return d.isoformat() if d else None

    dados = {
        "gerado_em": datetime.utcnow().isoformat() + "Z",
        "ajustes": {c.chave: c.valor for c in ConfigTerritorio.query.all()},
        "territorios": [{
            "numero": t.numero,
            "nome": t.nome,
            "etiqueta": t.etiqueta,
            "ativo": t.ativo,
            "ruas": t.ruas,
            "observacoes": t.observacoes,
            "area": json.loads(t.area) if t.area else None,
            "quadras": [{
                "numero": q.numero, "descricao": q.descricao,
                "link": q.link, "lat": q.lat, "lng": q.lng
            } for q in t.quadras]
        } for t in Territorio.query.order_by(Territorio.numero).all()],
        "dirigentes": [{
            "pessoa": d.pessoa.nome, "whatsapp": d.whatsapp
        } for d in DirigenteTerritorio.query.all()],
        "designacoes": [{
            "territorio": d.territorio.numero,
            "dirigente": d.dirigente,
            "data_saida": data(d.data_saida),
            "prazo": data(d.prazo),
            "data_devolucao": data(d.data_devolucao),
            "mapa_enviado_em": data(d.mapa_enviado_em),
            "observacao": d.observacao,
            "trabalhos": [{
                "data": data(t.data), "percentual": t.percentual,
                "observacao": t.observacao
            } for t in d.trabalhos]
        } for d in DesignacaoTerritorio.query.join(Territorio).order_by(DesignacaoTerritorio.data_saida).all()]
    }

    hoje = hoje_sao_paulo()

    return Response(
        json.dumps(dados, ensure_ascii=False, indent=2),
        mimetype="application/json",
        headers={
            "Content-Disposition":
                f"attachment; filename=backup-territorios-{hoje.isoformat()}.json"
        }
    )


@territorios.route("/zerar-historico", methods=["POST"])
@admin_required
def zerar_historico():
    if request.form.get("confirmacao", "").strip().upper() != "ZERAR":
        flash("Para zerar, digite ZERAR na caixa de confirmação.", "warning")
        return redirect(url_for("territorios.ajustes"))

    TrabalhoTerritorio.query.delete(synchronize_session=False)
    DesignacaoTerritorio.query.delete(synchronize_session=False)
    db.session.commit()

    flash("Histórico zerado. Todos os territórios estão disponíveis.", "success")
    return redirect(url_for("territorios.painel"))


# =========================================================
# FICHA DO DIRIGENTE (link enviado pelo WhatsApp, sem login)
# =========================================================

@ficha.route("/t/<codigo>")
def ver(codigo):
    designacao = DesignacaoTerritorio.query.filter_by(codigo=codigo).first()

    if not designacao:
        abort(404)

    territorio = designacao.territorio
    hoje = hoje_sao_paulo()

    resposta = render_template(
        "territorio_ficha.html",
        designacao=designacao,
        territorio=territorio,
        area=json.loads(territorio.area) if territorio.area else [],
        quadras_mapa=[
            {"numero": q.numero, "lat": q.lat, "lng": q.lng}
            for q in territorio.quadras
            if q.lat is not None and q.lng is not None
        ],
        rota=link_rota(territorio),
        percentuais=PERCENTUAIS,
        hoje=hoje,
        vencido=bool(
            designacao.data_devolucao is None
            and designacao.prazo and designacao.prazo < hoje
        )
    )

    return resposta, 200, {"X-Robots-Tag": "noindex", "Cache-Control": "no-store"}


@ficha.route("/t/<codigo>/trabalho", methods=["POST"])
def ficha_trabalho(codigo):
    designacao = DesignacaoTerritorio.query.filter_by(codigo=codigo).first()

    if not designacao:
        abort(404)

    voltar = redirect(url_for("ficha.ver", codigo=codigo) + "#registrar")

    if designacao.data_devolucao:
        flash("Este território já foi devolvido.", "warning")
        return voltar

    hoje = hoje_sao_paulo()
    data = ler_data(request.form.get("data"), hoje)
    percentual = ler_percentual(request.form.get("percentual"))

    if not percentual:
        flash("Escolha quanto foi trabalhado.", "warning")
        return voltar

    if data < designacao.data_saida or data > hoje:
        flash("Confira a data.", "warning")
        return voltar

    registrar_trabalho(designacao, data, percentual, request.form.get("observacao"))
    db.session.commit()

    flash(f"Obrigado! Registrado: {percentual}% trabalhado.", "success")
    return voltar


# =========================================================
# IMPORTAR (KML / KMZ do Google My Maps, ou GeoJSON)
# =========================================================

def ler_coordenadas_kml(texto):
    pontos = []

    for trecho in (texto or "").split():
        partes = trecho.split(",")

        try:
            lng, lat = float(partes[0]), float(partes[1])
        except (ValueError, IndexError):
            continue

        pontos.append([lat, lng])

    # O KML repete o primeiro ponto no final; não precisamos
    if len(pontos) > 1 and pontos[0] == pontos[-1]:
        pontos.pop()

    return pontos


def nome_tag(elemento):
    return elemento.tag.rsplit("}", 1)[-1]


def filhos(elemento, nome):
    return [e for e in elemento.iter() if nome_tag(e) == nome]


def texto_filho(elemento, nome):
    for filho in elemento:
        if nome_tag(filho) == nome:
            return (filho.text or "").strip()
    return ""


def ler_kml(conteudo):
    raiz = ET.fromstring(conteudo)
    itens = []

    for placemark in filhos(raiz, "Placemark"):
        nome = texto_filho(placemark, "name")
        descricao = texto_filho(placemark, "description")

        poligonos = []

        for poligono in filhos(placemark, "Polygon"):
            for externo in filhos(poligono, "outerBoundaryIs"):
                for coords in filhos(externo, "coordinates"):
                    pontos = ler_coordenadas_kml(coords.text)
                    if len(pontos) >= 3:
                        poligonos.append(pontos)

        # Linhas fechadas desenhadas como "linha" no My Maps
        if not poligonos:
            for linha in filhos(placemark, "LineString"):
                for coords in filhos(linha, "coordinates"):
                    pontos = ler_coordenadas_kml(coords.text)
                    if len(pontos) >= 3:
                        poligonos.append(pontos)

        if poligonos:
            itens.append({
                "tipo": "area", "nome": nome,
                "descricao": descricao, "poligonos": poligonos
            })
            continue

        for ponto in filhos(placemark, "Point"):
            for coords in filhos(ponto, "coordinates"):
                pontos = ler_coordenadas_kml(coords.text)
                if pontos:
                    itens.append({
                        "tipo": "ponto", "nome": nome,
                        "descricao": descricao, "ponto": pontos[0]
                    })

    return itens


def ler_geojson(conteudo):
    dados = json.loads(conteudo)
    itens = []

    if dados.get("type") == "FeatureCollection":
        features = dados.get("features", [])
    elif dados.get("type") == "Feature":
        features = [dados]
    else:
        features = []

    for feature in features:
        geometria = feature.get("geometry") or {}
        props = feature.get("properties") or {}
        nome = str(props.get("name") or props.get("nome") or "")
        descricao = str(props.get("description") or props.get("descricao") or "")
        tipo = geometria.get("type")
        coords = geometria.get("coordinates") or []

        if tipo == "Polygon":
            lista = [coords]
        elif tipo == "MultiPolygon":
            lista = coords
        elif tipo == "Point":
            itens.append({
                "tipo": "ponto", "nome": nome, "descricao": descricao,
                "ponto": [coords[1], coords[0]]
            })
            continue
        else:
            continue

        poligonos = []

        for poligono in lista:
            if not poligono:
                continue
            pontos = [[p[1], p[0]] for p in poligono[0]]
            if len(pontos) > 1 and pontos[0] == pontos[-1]:
                pontos.pop()
            if len(pontos) >= 3:
                poligonos.append(pontos)

        if poligonos:
            itens.append({
                "tipo": "area", "nome": nome,
                "descricao": descricao, "poligonos": poligonos
            })

    return itens


def separar_numero(nome):
    """'12 — Jardim Berlim' → (12, 'Jardim Berlim')"""
    achado = re.match(
        r"^\s*(?:territ[óo]rio\s*)?(\d+)\s*[-–—:.)]*\s*(.*)$",
        nome or "",
        re.IGNORECASE
    )

    if achado:
        return int(achado.group(1)), achado.group(2).strip()

    return None, (nome or "").strip()


def limpar_html(texto):
    texto = re.sub(r"<br\s*/?>", "\n", texto or "", flags=re.IGNORECASE)
    texto = re.sub(r"<[^>]+>", "", texto)
    return texto.strip()


@territorios.route("/importar", methods=["GET", "POST"])
@admin_required
def importar():
    if request.method == "GET":
        return render_template("territorios_importar.html")

    arquivo = request.files.get("arquivo")

    if not arquivo or not arquivo.filename:
        flash("Escolha um arquivo.", "warning")
        return redirect(url_for("territorios.importar"))

    conteudo = arquivo.read(TAMANHO_MAXIMO_IMPORTACAO + 1)

    if len(conteudo) > TAMANHO_MAXIMO_IMPORTACAO:
        flash("Arquivo muito grande (máximo 5 MB).", "warning")
        return redirect(url_for("territorios.importar"))

    nome_arquivo = arquivo.filename.lower()

    try:
        if nome_arquivo.endswith(".kmz") or conteudo[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(conteudo)) as pacote:
                kmls = [n for n in pacote.namelist() if n.lower().endswith(".kml")]

                if not kmls:
                    raise ValueError("sem kml")

                principal = "doc.kml" if "doc.kml" in kmls else kmls[0]
                itens = ler_kml(pacote.read(principal))

        elif nome_arquivo.endswith((".geojson", ".json")):
            itens = ler_geojson(conteudo.decode("utf-8-sig"))

        else:
            itens = ler_kml(conteudo)

    except (ValueError, ET.ParseError, zipfile.BadZipFile, UnicodeDecodeError, AttributeError):
        flash(
            "Não consegui ler esse arquivo. Envie um KML ou KMZ exportado "
            "do Google My Maps, ou um GeoJSON.",
            "danger"
        )
        return redirect(url_for("territorios.importar"))

    areas = [i for i in itens if i["tipo"] == "area"]
    pontos = [i for i in itens if i["tipo"] == "ponto"]

    if not areas:
        flash("O arquivo não tem nenhuma área desenhada.", "warning")
        return redirect(url_for("territorios.importar"))

    atualizar = request.form.get("atualizar") == "1"

    criados = atualizados = ignorados = 0
    importados = []
    numero_livre = proximo_numero()

    # Áreas com número no nome primeiro, para que as sem número
    # não "roubem" um número que aparece mais adiante no arquivo.
    areas.sort(key=lambda a: separar_numero(a["nome"])[0] is None)

    for area in areas:
        numero, nome = separar_numero(area["nome"])

        existente = (
            Territorio.query.filter_by(numero=numero).first()
            if numero else None
        )

        if existente and not atualizar:
            ignorados += 1
            continue

        if existente:
            territorio = existente
            atualizados += 1
        else:
            if not numero or any(t.numero == numero for t in importados):
                numero = numero_livre
            numero_livre = max(numero_livre, numero + 1)

            territorio = Territorio(numero=numero, nome=nome or f"Território {numero}")
            db.session.add(territorio)
            criados += 1

        if nome:
            territorio.nome = nome[:150]

        territorio.area = ler_area(json.dumps(area["poligonos"]))

        descricao = limpar_html(area["descricao"])
        if descricao:
            territorio.observacoes = descricao

        importados.append(territorio)

    # Pontos (marcadores) viram quadras do território
    # cuja área contém o ponto.
    quadras_novas = 0

    # Ao atualizar um território que já existia, as quadras
    # do arquivo substituem as antigas.
    if pontos:
        for territorio in importados:
            if territorio.id is not None:
                territorio.quadras.clear()

    for ponto in pontos:
        lat, lng = ponto["ponto"]

        for territorio in importados:
            poligonos = json.loads(territorio.area or "[]")

            if any(dentro_do_poligono(lat, lng, p) for p in poligonos):
                territorio.quadras.append(QuadraTerritorio(
                    ordem=len(territorio.quadras),
                    numero=(ponto["nome"] or str(len(territorio.quadras) + 1))[:20],
                    descricao=limpar_html(ponto["descricao"])[:200] or None,
                    lat=lat,
                    lng=lng
                ))
                quadras_novas += 1
                break

    db.session.commit()

    partes = []
    if criados:
        partes.append(f"{criados} criado{'s' if criados != 1 else ''}")
    if atualizados:
        partes.append(f"{atualizados} atualizado{'s' if atualizados != 1 else ''}")
    if ignorados:
        partes.append(f"{ignorados} já existia{'m' if ignorados != 1 else ''} (ignorado{'s' if ignorados != 1 else ''})")
    if quadras_novas:
        partes.append(f"{quadras_novas} quadra{'s' if quadras_novas != 1 else ''}")

    flash("Importação concluída: " + ", ".join(partes) + ".", "success")
    return redirect(url_for("territorios.painel"))
