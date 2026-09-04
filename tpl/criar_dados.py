from datetime import date, time, timedelta

from app import create_app
from app.models import db, Carrinho, Local, Programacao


app = create_app()


def hora(valor):
    """
    Converte textos como:
    7h
    7h30
    19h30
    """

    valor = valor.lower().replace(" ", "")

    if "h" not in valor:
        raise ValueError(f"Horário inválido: {valor}")

    partes = valor.split("h")

    h = int(partes[0])

    if partes[1] == "":
        m = 0
    else:
        m = int(partes[1])

    return time(h, m)


# =========================================================
# PROGRAMAÇÃO DA TABELA
# =========================================================

tabela = {

    "SEG": {
        1: ("Praça (Centro)", [
            ("7h", "8h"),
            ("8h", "10h"),
            ("10h", "11h"),
            ("14h", "15h"),
            ("15h", "16h"),
            ("16h", "17h"),
            ("17h", "18h"),
            ("18h", "19h"),
        ]),

        2: ("Rodoviária", [
            ("7h", "8h"),
            ("8h", "10h"),
            ("10h", "11h"),
            ("14h", "15h"),
            ("15h", "16h"),
            ("16h", "17h"),
            ("17h", "18h"),
            ("18h", "19h"),
        ]),

        3: ("Parque Bergamasco", [
            ("7h", "8h"),
            ("8h", "10h"),
            ("10h", "11h"),
            ("14h", "15h"),
            ("15h", "16h"),
            ("16h", "17h"),
            ("17h", "18h"),
            ("18h", "19h"),
        ]),
    },


    "TER": {
        1: ("Rodoviária", [
            ("6h", "8h"),
            ("8h", "10h"),
            ("10h", "11h"),
            ("14h", "15h"),
            ("15h", "16h"),
            ("16h", "17h"),
            ("18h", "19h"),
            ("19h", "20h"),
        ]),

        2: ("Praça (Centro)", [
            ("6h", "8h"),
            ("8h", "10h"),
            ("10h", "11h"),
            ("14h", "15h"),
            ("15h", "16h"),
            ("16h", "17h"),
            ("18h", "19h"),
            ("19h", "20h"),
        ]),

        3: ("Parque Bergamasco", [
            ("6h", "8h"),
            ("8h", "10h"),
            ("10h", "11h"),
            ("14h", "15h"),
            ("15h", "16h"),
            ("16h", "17h"),
            ("18h", "19h"),
            ("19h", "20h"),
        ]),
    },


    "QUA": {
        1: ("Rodoviária", [
            ("7h", "8h"),
            ("8h", "9h"),
            ("9h", "10h"),
            ("10h", "11h"),
            ("14h", "15h"),
            ("15h", "16h"),
            ("16h", "17h"),
            ("17h", "18h"),
            ("18h", "19h"),
            ("19h", "20h"),
        ]),

        2: ("Parque Bergamasco", [
            ("7h", "8h"),
            ("8h", "9h"),
            ("9h", "10h"),
            ("10h", "11h"),
            ("14h", "15h"),
            ("15h", "16h"),
            ("16h", "17h"),
            ("17h", "18h"),
            ("18h", "19h"),
            ("19h", "20h"),
        ]),

        3: ("Parque St. Maria", [
            ("7h", "8h"),
            ("8h", "9h"),
            ("9h", "10h"),
            ("10h", "11h"),
            ("14h", "15h"),
            ("15h", "16h"),
            ("16h", "17h"),
            ("17h", "18h"),
            ("18h", "19h"),
            ("19h", "20h"),
        ]),
    },


    "QUI": {
        1: ("Praça (Centro)", [
            ("6h", "7h"),
            ("7h", "8h"),
            ("8h", "9h"),
            ("9h", "10h"),
            ("10h", "11h"),
            ("14h", "15h"),
            ("15h", "16h"),
            ("16h", "18h"),
            ("18h", "19h"),
        ]),

        2: ("Rodoviária", [
            ("6h", "7h"),
            ("7h", "8h"),
            ("8h", "9h"),
            ("9h", "10h"),
            ("10h", "11h"),
            ("14h", "15h"),
            ("15h", "16h"),
            ("16h", "18h"),
            ("18h", "19h"),
        ]),

        3: ("Parque Bergamasco", [
            ("6h", "7h"),
            ("7h", "8h"),
            ("8h", "9h"),
            ("9h", "10h"),
            ("10h", "11h"),
            ("14h", "15h"),
            ("15h", "16h"),
            ("16h", "18h"),
            ("18h", "19h"),
        ]),
    },


    "SEX": {
        1: ("Rodoviária", [
            ("7h", "8h"),
            ("8h", "9h"),
            ("9h", "10h"),
            ("10h", "11h"),
            ("15h", "17h"),
            ("18h", "20h"),
        ]),

        2: ("Praça Centro", [
            ("7h", "8h"),
            ("8h", "9h"),
            ("9h", "10h"),
            ("10h", "11h"),
            ("15h", "17h"),
            ("18h", "20h"),
        ]),

        3: ("Rodoviária", [
            ("7h", "8h"),
            ("8h", "9h"),
            ("9h", "10h"),
            ("10h", "11h"),
            ("15h", "17h"),
            ("18h", "20h"),
        ]),
    },


    "SAB": {
        1: ("Praça (Centro)", [
            ("7h", "9h"),
            ("9h", "11h"),
            ("11h", "14h"),
        ]),

        2: ("Feira/Praça", [
            ("7h", "9h"),
            ("9h", "11h"),
            ("11h", "14h"),
        ]),

        3: ("Rodoviária", [
            ("7h", "9h"),
            ("9h", "11h"),
            ("11h", "14h"),
        ]),
    },


    "DOM": {
        1: ("Rodoviária", [
            ("8h", "10h"),
            ("10h", "11h"),
            ("14h", "16h"),
            ("16h", "17h"),
            ("18h", "19h30"),
        ]),

        2: ("Bar da Estação", [
            ("8h", "10h"),
            ("10h", "11h"),
            ("14h", "16h"),
            ("16h", "17h"),
            ("18h", "19h30"),
        ]),

        3: ("Parque Bergamasco", [
            ("8h", "10h"),
            ("10h", "11h"),
            ("14h", "16h"),
            ("16h", "17h"),
            ("18h", "19h30"),
        ]),
    }
}


dias = {
    "SEG": 0,
    "TER": 1,
    "QUA": 2,
    "QUI": 3,
    "SEX": 4,
    "SAB": 5,
    "DOM": 6,
}


# =========================================================
# CADASTRAR
# =========================================================

with app.app_context():

    # Segunda-feira da semana de teste
    segunda = date(2026, 8, 31)

    criados = 0

    for nome_dia, dados_carrinhos in tabela.items():

        data = segunda + timedelta(
            days=dias[nome_dia]
        )

        print()
        print(nome_dia, data)

        for numero, dados in dados_carrinhos.items():

            nome_local, horarios = dados

            # Carrinho
            carrinho = Carrinho.query.filter_by(
                nome=f"Carrinho {numero}"
            ).first()

            if not carrinho:

                carrinho = Carrinho(
                    nome=f"Carrinho {numero}"
                )

                db.session.add(carrinho)
                db.session.flush()


            # Local
            local = Local.query.filter_by(
                nome=nome_local
            ).first()

            if not local:

                local = Local(
                    nome=nome_local
                )

                db.session.add(local)
                db.session.flush()


            # Horários
            for inicio, fim in horarios:

                hora_inicio = hora(inicio)
                hora_fim = hora(fim)

                existe = Programacao.query.filter_by(
                    data=data,
                    hora_inicio=hora_inicio,
                    hora_fim=hora_fim,
                    carrinho_id=carrinho.id
                ).first()

                if existe:
                    continue


                programacao = Programacao(
                    data=data,
                    hora_inicio=hora_inicio,
                    hora_fim=hora_fim,
                    carrinho_id=carrinho.id,
                    local_id=local.id,
                    quantidade_maxima=3
                )

                db.session.add(programacao)

                criados += 1

    db.session.commit()


    print()
    print("===================================")
    print(f"{criados} horários criados.")
    print("Programação cadastrada com sucesso!")
    print("===================================")