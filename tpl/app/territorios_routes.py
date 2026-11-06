import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, timedelta

from flask import (
    Blueprint,
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
    Territorio,
    QuadraTerritorio,
    DesignacaoTerritorio
)

from app.routes import admin_required, hoje_sao_paulo


territorios = Blueprint(
    "territorios",
    __name__,
    url_prefix="/admin/territorios"
)

ETIQUETAS = ["Residencial", "Comercial", "Rural"]

STATUS_NOMES = {
    "disponivel": "Disponível",
    "em_campo": "Em campo",
    "vencido": "Prazo vencido"
}

# Prazo padrão para devolver um território
PRAZO_PADRAO_DIAS = 120

# Sem trabalhar há mais que isso → "precisa de atenção"
DIAS_SEM_TRABALHAR = 180

TAMANHO_MAXIMO_IMPORTACAO = 5 * 1024 * 1024  # 5 MB


# =========================================================
# FUNÇÕES AUXILIARES
# =========================================================

def ler_data(texto, padrao=None):
    try:
        return date.fromisoformat((texto or "").strip())
    except ValueError:
        return padrao


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

        def campo(lista):
            return lista[i].strip() if i < len(lista) else ""

        def coordenada(lista):
            try:
                return float(campo(lista))
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


# =========================================================
# PAINEL
# =========================================================

@territorios.route("/")
@admin_required
def painel():
    hoje = hoje_sao_paulo()

    lista = (
        Territorio.query
        .filter_by(ativo=True)
        .order_by(Territorio.numero)
        .all()
    )

    contagem = {"disponivel": 0, "em_campo": 0, "vencido": 0}

    for territorio in lista:
        contagem[territorio.status(hoje)] += 1

    # Precisa de atenção: prazo vencido, ou disponível e
    # sem ser trabalhado há muito tempo (ou nunca).
    atencao = []

    for territorio in lista:
        status = territorio.status(hoje)

        if status == "vencido":
            atual = territorio.designacao_atual
            dias = (hoje - atual.prazo).days
            atencao.append((
                territorio,
                f"Prazo venceu há {dias} dia{'s' if dias != 1 else ''} · {atual.dirigente}",
                0,
                -dias
            ))

        elif status == "disponivel":
            ultima = territorio.ultima_devolucao

            if ultima is None:
                atencao.append((territorio, "Nunca foi trabalhado", 1, 0))

            elif (hoje - ultima).days > DIAS_SEM_TRABALHAR:
                meses = (hoje - ultima).days // 30
                atencao.append((
                    territorio,
                    f"Sem trabalhar há {meses} meses",
                    2,
                    -(hoje - ultima).days
                ))

    atencao.sort(key=lambda item: (item[2], item[3]))

    # Cobertura: territórios devolvidos em cada uma das
    # últimas 8 semanas (segunda a domingo).
    inicio_semana = hoje - timedelta(days=hoje.weekday())
    semanas = []

    for i in range(7, -1, -1):
        inicio = inicio_semana - timedelta(weeks=i)
        fim = inicio + timedelta(days=6)

        total = DesignacaoTerritorio.query.filter(
            DesignacaoTerritorio.data_devolucao >= inicio,
            DesignacaoTerritorio.data_devolucao <= fim
        ).count()

        semanas.append({
            "rotulo": inicio.strftime("%d/%m"),
            "total": total
        })

    maior_semana = max([s["total"] for s in semanas] + [1])

    # Quantos territórios foram trabalhados nos últimos 12 meses
    um_ano = hoje - timedelta(days=365)
    trabalhados_ano = sum(
        1 for t in lista
        if t.ultima_devolucao and t.ultima_devolucao >= um_ano
    )

    return render_template(
        "territorios_painel.html",
        territorios=lista,
        contagem=contagem,
        atencao=atencao[:8],
        semanas=semanas,
        maior_semana=maior_semana,
        trabalhados_ano=trabalhados_ano,
        mapa=dados_mapa(lista, hoje),
        hoje=hoje,
        status_nomes=STATUS_NOMES,
        filtro=request.args.get("status", "")
    )


# =========================================================
# DETALHE
# =========================================================

@territorios.route("/<int:territorio_id>")
@admin_required
def detalhe(territorio_id):
    territorio = Territorio.query.get_or_404(territorio_id)
    hoje = hoje_sao_paulo()

    centro = centro_area(territorio.area)

    pessoas = (
        Pessoa.query
        .filter_by(ativo=True)
        .order_by(Pessoa.nome)
        .all()
    )

    return render_template(
        "territorio_detalhe.html",
        territorio=territorio,
        status=territorio.status(hoje),
        status_nomes=STATUS_NOMES,
        area=json.loads(territorio.area) if territorio.area else [],
        quadras_mapa=[
            {"numero": q.numero, "lat": q.lat, "lng": q.lng}
            for q in territorio.quadras
            if q.lat is not None and q.lng is not None
        ],
        rota=(
            "https://www.google.com/maps/dir/?api=1&destination="
            f"{centro[0]:.6f},{centro[1]:.6f}"
        ) if centro else None,
        pessoas=pessoas,
        hoje=hoje,
        prazo_padrao=hoje + timedelta(days=PRAZO_PADRAO_DIAS)
    )


# =========================================================
# CADASTRO E EDIÇÃO
# =========================================================

def proximo_numero():
    ultimo = db.session.query(db.func.max(Territorio.numero)).scalar()
    return (ultimo or 0) + 1


def outros_para_mapa(territorio_id=None):
    """Áreas dos outros territórios, em cinza, para referência."""
    hoje = hoje_sao_paulo()
    lista = Territorio.query.filter(Territorio.area.isnot(None))

    if territorio_id:
        lista = lista.filter(Territorio.id != territorio_id)

    return dados_mapa(lista.all(), hoje)


@territorios.route("/novo", methods=["GET", "POST"])
@admin_required
def novo():
    if request.method == "POST":
        return salvar(Territorio())

    return render_template(
        "territorio_form.html",
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

    db.session.delete(territorio)
    db.session.commit()

    flash(f"Território {territorio.numero} excluído.", "success")
    return redirect(url_for("territorios.painel"))


# =========================================================
# DESIGNAÇÕES
# =========================================================

@territorios.route("/<int:territorio_id>/designar", methods=["POST"])
@admin_required
def designar(territorio_id):
    territorio = Territorio.query.get_or_404(territorio_id)
    hoje = hoje_sao_paulo()
    voltar = redirect(url_for("territorios.detalhe", territorio_id=territorio.id))

    if territorio.designacao_atual:
        flash("Este território já está em campo. Registre a devolução primeiro.", "warning")
        return voltar

    try:
        pessoa = db.session.get(Pessoa, int(request.form.get("pessoa_id", "")))
    except ValueError:
        pessoa = None

    if not pessoa:
        flash("Escolha o dirigente.", "warning")
        return voltar

    saida = ler_data(request.form.get("data_saida"), hoje)
    prazo = ler_data(
        request.form.get("prazo"),
        saida + timedelta(days=PRAZO_PADRAO_DIAS)
    )

    if prazo < saida:
        flash("O prazo não pode ser antes da data de saída.", "warning")
        return voltar

    db.session.add(DesignacaoTerritorio(
        territorio_id=territorio.id,
        pessoa_id=pessoa.id,
        dirigente_nome=pessoa.nome,
        data_saida=saida,
        prazo=prazo,
        observacao=request.form.get("observacao", "").strip()[:300] or None,
        criado_por_id=current_user.id
    ))
    db.session.commit()

    flash(f"Território {territorio.numero} designado para {pessoa.nome}.", "success")
    return voltar


@territorios.route("/<int:territorio_id>/devolver", methods=["POST"])
@admin_required
def devolver(territorio_id):
    territorio = Territorio.query.get_or_404(territorio_id)
    voltar = redirect(url_for("territorios.detalhe", territorio_id=territorio.id))

    atual = territorio.designacao_atual

    if not atual:
        flash("Este território não está em campo.", "warning")
        return voltar

    devolucao = ler_data(request.form.get("data_devolucao"), hoje_sao_paulo())

    if devolucao < atual.data_saida:
        flash("A devolução não pode ser antes da data de saída.", "warning")
        return voltar

    atual.data_devolucao = devolucao
    db.session.commit()

    flash(f"Território {territorio.numero} devolvido.", "success")
    return voltar


@territorios.route("/<int:territorio_id>/prazo", methods=["POST"])
@admin_required
def alterar_prazo(territorio_id):
    territorio = Territorio.query.get_or_404(territorio_id)
    voltar = redirect(url_for("territorios.detalhe", territorio_id=territorio.id))

    atual = territorio.designacao_atual
    prazo = ler_data(request.form.get("prazo"))

    if not atual or not prazo:
        flash("Informe o novo prazo.", "warning")
        return voltar

    if prazo < atual.data_saida:
        flash("O prazo não pode ser antes da data de saída.", "warning")
        return voltar

    atual.prazo = prazo
    db.session.commit()

    flash("Prazo atualizado.", "success")
    return voltar


@territorios.route("/designacao/<int:designacao_id>/excluir", methods=["POST"])
@admin_required
def excluir_designacao(designacao_id):
    designacao = DesignacaoTerritorio.query.get_or_404(designacao_id)
    territorio_id = designacao.territorio_id

    db.session.delete(designacao)
    db.session.commit()

    flash("Registro removido do histórico.", "success")
    return redirect(url_for("territorios.detalhe", territorio_id=territorio_id))


# =========================================================
# HISTÓRICO
# =========================================================

@territorios.route("/historico")
@admin_required
def historico():
    designacoes = (
        DesignacaoTerritorio.query
        .join(Territorio)
        .order_by(DesignacaoTerritorio.data_saida.desc(), DesignacaoTerritorio.id.desc())
        .limit(300)
        .all()
    )

    em_campo = [d for d in designacoes if d.data_devolucao is None]

    # Dirigentes com território em mãos
    por_dirigente = {}

    for designacao in em_campo:
        por_dirigente.setdefault(designacao.dirigente, []).append(designacao)

    return render_template(
        "territorios_historico.html",
        designacoes=designacoes,
        por_dirigente=sorted(por_dirigente.items()),
        hoje=hoje_sao_paulo()
    )


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
