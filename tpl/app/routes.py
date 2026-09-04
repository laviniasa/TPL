from datetime import date, timedelta

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for
)

from app.models import (
    db,
    Programacao,
    Reserva,
    ReservaParticipante,
    Carrinho,
    Local,
    Pessoa
)


routes = Blueprint("routes", __name__)


# =========================================================
# FUNÇÃO AUXILIAR
# =========================================================

def pessoas_da_reserva(reserva):

    nomes = [reserva.nome_principal]

    for participante in reserva.participantes:
        nomes.append(participante.nome)

    return nomes


# =========================================================
# AGENDA
# =========================================================

@routes.route("/")
def agenda():

    data_parametro = request.args.get("data")

    if data_parametro:
        data_selecionada = date.fromisoformat(
            data_parametro
        )
    else:
        data_selecionada = date.today()


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


    return render_template(

        "agenda.html",

        carrinhos=carrinhos_lista,

        dias_semana=dias_semana,

        data_selecionada=data_selecionada,

        segunda=segunda,

        timedelta=timedelta

    )


# =========================================================
# RESERVAR HORÁRIO
# =========================================================

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

        reserva = Reserva(

            nome_principal=nome_principal,

            tipo="UNICA",

            data_inicio=programacao.data,

            data_fim=programacao.data,

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


    return render_template(

        "participar.html",

        programacao=programacao,

        pessoas=pessoas

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